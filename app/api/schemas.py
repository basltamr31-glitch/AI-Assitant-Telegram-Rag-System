"""Request and response shapes for the internal API.

Why declare these at all, when n8n could post any JSON it likes?
----------------------------------------------------------------
Because a schema is a contract, and a contract is the cheapest kind of test.
When n8n sends `user_id` as the string "7431125996" instead of a number,
FastAPI rejects it at the door with a precise 422 naming the field - rather
than letting it travel three layers deep and fail as a confusing TypeError
inside the allowlist check.

The rule this project follows: *validate at the boundary, trust inside it*.
Everything past this module can assume the data is well-formed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """One incoming Telegram message, as n8n forwards it."""

    # Telegram ids are int64. Python ints are unbounded, so no overflow worry.
    chat_id: int = Field(description="Where the reply must be sent")
    user_id: int = Field(description="Who sent it - checked against the allowlist")
    first_name: str = Field(default="", max_length=128)
    text: str = Field(default="", max_length=4096)
    message_id: int | None = None

    # Reject unknown keys instead of silently ignoring them. If n8n starts
    # sending a field we did not plan for, we want to find out immediately,
    # not discover in Phase 9 that it was being dropped all along.
    model_config = {"extra": "forbid"}


class ChatResponse(BaseModel):
    """What Python tells n8n to send back."""

    reply: str
    # Python owns the formatting decision, not the workflow. Phase 5 will
    # return Markdown for some answers and plain text for others; n8n should
    # not have to know which.
    parse_mode: str = "HTML"
    # Which branch produced this reply. Useless to the user, invaluable in
    # logs when you are asking "why did it answer that?"
    handled_by: str
    trace_id: str


class HealthResponse(BaseModel):
    status: str
    env: str
    version: str
    # "ready" or "unavailable". Says whether the service can reach Claude
    # without revealing anything about the key itself.
    llm: str = "unknown"
