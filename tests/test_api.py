"""Tests for the internal API.

These never start a server and never touch the network. `TestClient` calls the
application in-process, which is why the whole file runs in well under a
second - fast enough that you run it after every edit instead of hoping.

Most of what is tested here is refusal. That is deliberate: the interesting
question about an endpoint exposed through a public tunnel is not "does it
work when everything is right", it is "what does it do when something is
wrong". Every 401 and 403 below is a door someone will eventually push on.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

GOOD_KEY = "test-key-do-not-use-in-production"
OWNER_ID = 7431125996
STRANGER_ID = 1111111111


class FakeLLM:
    """Stands in for AnthropicClient.

    Unit tests must not depend on a network, an API key, or what a model feels
    like saying today. This returns exactly what the test asks for, including
    failure.
    """

    def __init__(self, text: str = "A plain answer.", fails: bool = False) -> None:
        self.text = text
        self.fails = fails
        self.calls: list[str] = []

    async def complete(self, system: str, user_message: str, max_tokens: int = 1024):
        self.calls.append(user_message)
        if self.fails:
            raise RuntimeError("upstream is down")
        from app.llm.client import LLMResult

        return LLMResult(
            text=self.text,
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app built from known settings rather than the developer's .env."""
    monkeypatch.setenv("INTERNAL_API_KEY", GOOD_KEY)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER_ID))
    monkeypatch.setenv("APP_ENV", "development")
    # Settings are cached with lru_cache, so the patched environment only
    # takes effect once the cache is dropped - before *and* after, so this
    # test's values never leak into the next one.
    get_settings.cache_clear()

    from app.api.main import create_app

    with TestClient(create_app()) as c:
        # No API key in the test environment, so the app starts without a
        # model. Tests that need one set c.app.state.llm themselves.
        yield c

    get_settings.cache_clear()


def post(client: TestClient, key: str | None = GOOD_KEY, **body):
    payload = {"chat_id": 1, "user_id": OWNER_ID, "first_name": "Basel", "text": "hi"}
    payload.update(body)
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post("/v1/chat", json=payload, headers=headers)


# --- health ------------------------------------------------------------------


def test_healthz_needs_no_key(client: TestClient) -> None:
    """You must be able to ask "is it up?" without holding the secret."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_every_response_carries_a_trace_id(client: TestClient) -> None:
    assert client.get("/healthz").headers["X-Trace-Id"]


# --- authentication ----------------------------------------------------------


def test_missing_key_is_rejected(client: TestClient) -> None:
    assert post(client, key=None).status_code == 401


def test_wrong_key_is_rejected(client: TestClient) -> None:
    assert post(client, key="wrong").status_code == 401


def test_almost_right_key_is_rejected(client: TestClient) -> None:
    """A prefix of the real key must not pass - guards against sloppy compares."""
    assert post(client, key=GOOD_KEY[:-1]).status_code == 401


def test_unconfigured_key_closes_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty INTERNAL_API_KEY must never mean "let everyone in"."""
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER_ID))
    get_settings.cache_clear()

    from app.api.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        response = c.post(
            "/v1/chat",
            json={"chat_id": 1, "user_id": OWNER_ID, "first_name": "B", "text": "hi"},
            headers={"X-API-Key": ""},
        )
    get_settings.cache_clear()
    # 500, not 200: a misconfigured server refuses to serve.
    assert response.status_code == 500


# --- authorisation -----------------------------------------------------------


def test_stranger_is_forbidden_even_with_a_valid_key(client: TestClient) -> None:
    """The second layer: a correct key does not make you the owner."""
    assert post(client, user_id=STRANGER_ID).status_code == 403


def test_owner_is_allowed(client: TestClient) -> None:
    assert post(client).status_code == 200


# --- validation --------------------------------------------------------------


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    """Silent field-dropping is how contracts rot unnoticed."""
    assert post(client, surprise="value").status_code == 422


def test_non_numeric_user_id_is_rejected(client: TestClient) -> None:
    assert post(client, user_id="not-a-number").status_code == 422


# --- behaviour ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", "command.start"),
        ("/help", "command.help"),
        ("/ping", "command.ping"),
        ("/whoami", "command.whoami"),
        ("", "empty"),
    ],
)
def test_commands_are_answered_without_the_model(
    client: TestClient, text: str, expected: str
) -> None:
    """Commands must never spend tokens - that is the whole point of them."""
    fake = FakeLLM()
    client.app.state.llm = fake

    body = post(client, text=text).json()

    assert body["handled_by"] == expected
    assert body["reply"]
    assert fake.calls == [], "a command reached the model"


def test_ordinary_text_goes_to_the_model(client: TestClient) -> None:
    fake = FakeLLM(text="Paris is the capital of France.")
    client.app.state.llm = fake

    body = post(client, text="capital of France?").json()

    assert body["handled_by"] == "model"
    assert body["reply"] == "Paris is the capital of France."
    assert fake.calls == ["capital of France?"]


def test_model_output_is_sanitised_before_it_is_returned(client: TestClient) -> None:
    """The model is not trusted just because it is ours."""
    client.app.state.llm = FakeLLM(text="<h1>Title</h1> and 3 < 4 and <b>open")

    reply = post(client, text="format something").json()["reply"]

    assert "<h1>" not in reply
    assert "3 &lt; 4" in reply
    assert reply.endswith("</b>"), "an unclosed tag would break the send"


def test_a_failing_model_still_produces_a_message(client: TestClient) -> None:
    """Silence is the worst failure mode: the user cannot tell it from a hang."""
    client.app.state.llm = FakeLLM(fails=True)

    body = post(client, text="anything").json()

    assert body["handled_by"] == "model.error"
    assert body["reply"]


def test_an_empty_completion_does_not_send_an_empty_message(
    client: TestClient,
) -> None:
    """Telegram rejects an empty sendMessage, so we must not produce one."""
    client.app.state.llm = FakeLLM(text="   ")

    body = post(client, text="anything").json()

    assert body["handled_by"] == "model.empty"
    assert body["reply"].strip()


def test_without_a_key_commands_work_and_chat_explains_why(
    client: TestClient,
) -> None:
    """A missing ANTHROPIC_API_KEY degrades; it does not take the service down."""
    assert client.app.state.llm is None
    assert post(client, text="/ping").json()["handled_by"] == "command.ping"

    body = post(client, text="hello there").json()
    assert body["handled_by"] == "model.unavailable"
    assert "ANTHROPIC_API_KEY" in body["reply"]


def test_healthz_reports_model_availability(client: TestClient) -> None:
    assert client.get("/healthz").json()["llm"] == "unavailable"


def test_rejection_never_reveals_the_expected_key(client: TestClient) -> None:
    """A failed auth response must not help the caller guess the real key.

    (An earlier version of this test asserted that a reply never *echoes* the
    key. That was a bad test: if you type the key into Telegram yourself, the
    echo endpoint sends it back, and nothing has leaked - you already knew it.
    The property worth protecting is that the *server* never volunteers it.)
    """
    response = post(client, key="wrong")
    assert response.status_code == 401
    assert GOOD_KEY not in response.text
    # And the message must not distinguish "wrong key" from "no key".
    assert response.json()["detail"] == post(client, key=None).json()["detail"]
