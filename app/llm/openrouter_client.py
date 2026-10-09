"""One key, many models, through OpenRouter.

Why this provider
-----------------
ADR-015. Two reasons, both about Arabic:

* The local 1.7B model answers Arabic, but roughly - one reply mixed a
  Vietnamese word into an Arabic sentence. The knowledge base is Arabic legal
  and curriculum text, so a weak Arabic model would make every later
  retrieval measurement untrustworthy: you could not tell a bad chunk from a
  bad reader.
* OpenRouter's API is OpenAI-compatible, so this single adapter reaches
  hundreds of models. Changing which one answers becomes `OPENROUTER_MODEL`,
  with no code change at all - one step beyond what ADR-004 promised.

Cost
----
Prices vary per model and change without notice, so hardcoding them would
guarantee wrong numbers. We ask OpenRouter to return the actual cost of each
call (`usage.include`) and record that. A price table we forgot to update is
worse than no table, because it looks authoritative.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence

import httpx

from app.config import get_settings
from app.core.errors import ModelRejected, ModelUnavailable, QuotaExhausted, ServiceError
from app.core.logging import get_logger
from app.llm.base import (
    LLMResult,
    Message,
    ToolCall,
    ToolSpec,
    ToolStep,
    strip_thinking,
)

log = get_logger(__name__)

# Pauses before the second and third attempts. See _post_with_retries.
_RETRY_DELAYS_S = (3.0, 6.0)


class OpenRouterClient:
    """OpenAI-compatible chat completions, via OpenRouter."""

    def __init__(self) -> None:
        settings = get_settings()
        key = settings.openrouter_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set - add it to .env, or set "
                "LLM_PROVIDER to ollama."
            )
        self._model = settings.openrouter_model
        self._reasoning_effort = settings.openrouter_reasoning_effort.strip()
        self._fallbacks = [
            m.strip() for m in settings.openrouter_fallback_models.split(",") if m.strip()
        ]
        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {key}",
                # OpenRouter attributes usage to an app when these are sent.
                # Neither is a secret and both are optional.
                "HTTP-Referer": "https://github.com/basltamr31-glitch/AI-Assitant-Telegram-Rag-System",
                "X-Title": "Telegram AI Assistant",
            },
            # 75 s, not 120: two calls must fit inside n8n's 120 s, and a
            # healthy answer at low reasoning effort takes 30-40 s.
            timeout=httpx.Timeout(75.0, connect=10.0),
        )

    async def complete(
        self,
        system: str,
        user_message: str,
        # Generous on purpose. Reasoning models spend part of this budget on
        # thinking the reader never sees: Nemotron 3 Ultra used all of 1024
        # and stopped three lines into a legal answer. Free models bill
        # nothing, and a paid one bills what it uses, not what it may use.
        max_tokens: int = 4096,
        history: Sequence[Message] = (),
    ) -> LLMResult:
        messages = [
            {"role": "system", "content": system},
            *(m.as_dict() for m in history),
            {"role": "user", "content": user_message},
        ]
        return await self._chat(messages, max_tokens)

    async def complete_with_tools(
        self,
        system: str,
        user_message: str,
        *,
        tools: Sequence[ToolSpec],
        steps: Sequence[ToolStep] = (),
        history: Sequence[Message] = (),
        allow_tools: bool = True,
        max_tokens: int = 4096,
    ) -> LLMResult:
        """One turn of the agent loop, in OpenAI's tool-calling format.

        `steps` are the rounds already run this turn: each becomes an
        assistant message carrying its tool calls, then one `tool` message
        per result. `allow_tools=False` still sends the tool definitions -
        a transcript that mentions tools is rejected without them - but
        tells the model it must answer now.
        """
        messages: list[dict] = [
            {"role": "system", "content": system},
            *(m.as_dict() for m in history),
            {"role": "user", "content": user_message},
        ]
        for step in steps:
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in step.calls
                    ],
                }
            )
            messages.extend(
                {"role": "tool", "tool_call_id": call.id, "content": result}
                for call, result in zip(step.calls, step.results)
            )
        return await self._chat(
            messages,
            max_tokens,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
            tool_choice="auto" if allow_tools else "none",
        )

    async def _chat(
        self,
        messages: list[dict],
        max_tokens: int,
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResult:
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            # Ask for the real cost of this call rather than guessing it.
            "usage": {"include": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if self._reasoning_effort:
            # How long a reasoning model thinks before answering. Nemotron 3
            # Ultra at its default took two minutes on a follow-up with
            # history - past n8n's timeout. At "low": 31 seconds, shorter,
            # still correct and cited. Models that do not reason ignore it.
            payload["reasoning"] = {"effort": self._reasoning_effort}
        if self._fallbacks:
            # OpenRouter's own fallback: if the first model errors or is
            # overloaded, it tries the next within the same request. One
            # round trip, and `data["model"]` says which one answered.
            payload["models"] = [self._model, *self._fallbacks]

        started = time.perf_counter()
        data = await self._post_with_retries(payload)
        duration_ms = round((time.perf_counter() - started) * 1000)

        choice = data["choices"][0]
        message = choice["message"]
        text = strip_thinking(message.get("content") or "")
        usage = data.get("usage") or {}

        result = LLMResult(
            text=text,
            # What answered, which can differ from what was asked for when a
            # model is routed to a fallback.
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            stop_reason=choice.get("finish_reason"),
            # Reported cost wins over any local table; `local` stays False so
            # a free model still records a real, measured zero.
            reported_cost_usd=usage.get("cost"),
            tool_calls=tuple(_parse_tool_call(c) for c in message.get("tool_calls") or ()),
        )
        log.info(
            "llm.completion",
            provider="openrouter",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
            tool_calls=[c.name for c in result.tool_calls],
            duration_ms=duration_ms,
        )
        return result


    async def _post_with_retries(self, payload: dict) -> dict:
        """POST, and turn every way it can fail into a named error.

        What is retried, after 3 s and then 6 s: an overloaded upstream (a
        5xx, a 429 that is not the daily quota, or a 200 whose body is an
        error with no `choices` - the free upstreams do all three), and a
        connection that failed before anything was sent. One second later
        the overload was usually still there; a few seconds clears it.

        What is not: the day's quota (retrying cannot help and only makes
        the user wait), a rejected request (a bad key or model name is a
        configuration problem), and a timeout - a request that already took
        a minute, repeated, would take the user past n8n's own timeout and
        they would get nothing instead of an apology.
        """
        last: ServiceError | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS_S, None), start=1):
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                log.error("llm.timeout", attempt=attempt)
                raise ModelUnavailable("timed out") from exc
            except httpx.TransportError as exc:
                last = ModelUnavailable(f"transport: {type(exc).__name__}")
            else:
                data, last = _classify(response)
                if last is None:
                    if attempt > 1:
                        log.info("llm.recovered", attempt=attempt)
                    return data
                if not last.retryable:
                    raise last
            log.warning("llm.retrying", attempt=attempt, reason=str(last), next_in_s=delay)
            if delay is None:
                break
            await asyncio.sleep(delay)
        assert last is not None
        raise last


def _classify(response: httpx.Response) -> tuple[dict, ServiceError | None]:
    """Read a response as data, or as the error it represents."""
    body = response.text[:400]
    if response.status_code >= 400:
        # OpenRouter puts the useful part in the body; the status alone does
        # not distinguish "no credit" from "unknown model".
        log.error("llm.openrouter_error", status=response.status_code, body=body)
        if response.status_code == 429:
            if "free-models-per-day" in body:
                return {}, QuotaExhausted(body)
            return {}, ModelUnavailable("rate limited upstream")
        if response.status_code >= 500:
            return {}, ModelUnavailable(f"HTTP {response.status_code}")
        return {}, ModelRejected(f"HTTP {response.status_code}")
    data = response.json()
    if not data.get("choices"):
        # Usually "Upstream error from Nvidia: Service temporarily overloaded",
        # with a 200 status.
        log.warning("llm.openrouter_no_choices", body=body)
        return {}, ModelUnavailable("no choices")
    return data, None


def _parse_tool_call(raw: dict) -> ToolCall:
    """OpenAI sends arguments as a JSON *string*, which a model can get wrong.

    Malformed arguments become an empty dict rather than an exception: the
    tool then reports what is missing, and the model gets a chance to fix it,
    which is better than the whole message failing over a stray quote.
    """
    function = raw.get("function") or {}
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        log.warning("llm.bad_tool_arguments", raw=str(function.get("arguments"))[:200])
        arguments = {}
    return ToolCall(
        id=raw.get("id", ""),
        name=function.get("name", ""),
        arguments=arguments if isinstance(arguments, dict) else {},
    )
