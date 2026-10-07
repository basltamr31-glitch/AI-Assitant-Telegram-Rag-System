"""The Anthropic adapter.

ADR-004 put the model behind an adapter rather than calling the SDK from the
request handler, for two reasons that both arrive later:

* **Phase 10** needs a tool-use loop. That belongs here, not scattered through
  the API layer.
* **Swapping models** should be one config line. `responder.py` asks for a
  completion and receives text and a token count; it never learns which
  vendor produced them.

Cost is measured from the first call, not bolted on in Phase 14. A number you
only start collecting once it hurts has no history to compare against, and
"is retrieval worth the extra tokens?" is unanswerable without one.
"""

from __future__ import annotations

from collections.abc import Sequence

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import PRICING, LLMResult, Message

log = get_logger(__name__)

class AnthropicClient:
    """Thin async wrapper around the Anthropic Messages API."""

    def __init__(self) -> None:
        settings = get_settings()
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            # Fail here, at startup, rather than on the first user message.
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set - add it to .env before Phase 5."
            )
        self._client = AsyncAnthropic(api_key=key)
        self._model = settings.anthropic_model

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1024,
        history: Sequence[Message] = (),
    ) -> LLMResult:
        """Send a message, after the earlier turns of the conversation.

        `history` is oldest first and must start with a user turn - the
        Messages API rejects anything else, and `responder.py` guarantees it.
        """
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                *(m.as_dict() for m in history),
                {"role": "user", "content": user_message},
            ],
        )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        result = LLMResult(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )
        log.info(
            "llm.completion",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
            # The prompt and the reply are not logged, for the same reason the
            # message text is not: logs travel, conversations should not.
        )
        return result


def create_llm_client():
    """Build whichever provider `.env` selected.

    Imported lazily so that choosing Ollama does not require the Anthropic SDK
    to be configured, and vice versa. A missing key or a stopped Ollama server
    should be *that* provider's problem, not a startup failure for both.
    """
    provider = get_settings().llm_provider
    if provider == "ollama":
        from app.llm.ollama_client import OllamaClient

        return OllamaClient()
    if provider == "openrouter":
        from app.llm.openrouter_client import OpenRouterClient

        return OpenRouterClient()
    return AnthropicClient()
