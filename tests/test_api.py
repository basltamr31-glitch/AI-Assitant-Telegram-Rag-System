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
        ("just talking", "echo"),
        ("", "empty"),
    ],
)
def test_each_branch_is_reachable(client: TestClient, text: str, expected: str) -> None:
    body = post(client, text=text).json()
    assert body["handled_by"] == expected
    assert body["reply"]


def test_user_text_is_html_escaped(client: TestClient) -> None:
    """The reply is sent as HTML, so echoed text must not be able to inject it."""
    body = post(client, text="<b>bold</b>").json()
    assert "<b>bold</b>" not in body["reply"]
    assert "&lt;b&gt;bold&lt;/b&gt;" in body["reply"]


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
