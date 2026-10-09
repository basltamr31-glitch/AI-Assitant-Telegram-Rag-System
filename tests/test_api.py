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
from types import SimpleNamespace

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

    def __init__(
        self,
        text: str = "A plain answer.",
        fails: bool = False,
        replies: list[str] | None = None,
    ) -> None:
        self.text = text
        # One reply per call, in order, for tests of what happens on a retry.
        self.replies = list(replies or [])
        self.fails = fails
        self.calls: list[str] = []
        self.systems: list[str] = []
        self.histories: list[list] = []

    async def complete(
        self, system: str, user_message: str, max_tokens: int = 1024, history=()
    ):
        self.calls.append(user_message)
        self.histories.append(list(history))
        self.systems.append(system)
        if self.fails:
            raise RuntimeError("upstream is down")
        from app.llm.client import LLMResult

        return LLMResult(
            text=self.replies.pop(0) if self.replies else self.text,
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
    # Pin the model provider off. Without this the suite reads whatever key
    # happens to be in the developer's .env, and "does a missing key degrade
    # gracefully?" quietly stops being tested the day someone adds one.
    # Environment variables outrank the .env file in pydantic-settings, so an
    # empty string here wins.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    # Grounding off by default here, so these tests keep exercising the model
    # path directly. The grounded path has its own fixture below, with a fake
    # retriever - mixing the two would mean every assertion about a reply
    # depended on a search result as well.
    monkeypatch.setenv("RETRIEVAL_ENABLED", "false")
    # Memory off here too: these tests must never write to a real Postgres.
    monkeypatch.setenv("MEMORY_ENABLED", "false")
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
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


# --- Phase 8: the grounded path ----------------------------------------------


class FakeRetrieved:
    def __init__(self, passages: list[str]) -> None:
        self._passages = passages
        self.threshold = 0.45
        self.best_rejected_score = 0.41 if not passages else None
        # The responder logs results[0].score, so the stand-ins need one.
        self.results = [SimpleNamespace(score=0.9 - i * 0.1)
                        for i in range(len(passages))]

    @property
    def found(self) -> bool:
        return bool(self._passages)

    def as_context(self, max_chars: int = 6000) -> str:
        return "\n\n".join(
            f"[{i}] source.pdf, p.{i}\n{p}" for i, p in enumerate(self._passages, 1)
        )

    def citations(self) -> list[str]:
        return [f"source.pdf, p.{i}" for i in range(1, len(self._passages) + 1)]


class FakeRetriever:
    def __init__(self, passages: list[str] | None = None, fails: bool = False) -> None:
        self.passages = passages or []
        self.fails = fails
        self.queries: list[str] = []
        self.contexts: list[str | None] = []

    def retrieve(self, query: str, **kwargs):
        self.queries.append(query)
        self.contexts.append(kwargs.get("context"))
        if self.fails:
            raise RuntimeError("qdrant is down")
        return FakeRetrieved(self.passages)


@pytest.fixture
def grounded_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERNAL_API_KEY", GOOD_KEY)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER_ID))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_WARM_ON_START", "false")
    monkeypatch.setenv("MEMORY_ENABLED", "false")
    get_settings.cache_clear()

    from app.api.main import create_app

    with TestClient(create_app()) as c:
        yield c

    get_settings.cache_clear()


def test_a_grounded_answer_cites_its_sources(grounded_client: TestClient) -> None:
    grounded_client.app.state.llm = FakeLLM(text="باريس هي العاصمة [1].")
    grounded_client.app.state.retriever = FakeRetriever(["نص عن فرنسا"])

    body = post(grounded_client, text="ما هي عاصمة فرنسا؟").json()

    assert body["handled_by"] == "model.grounded"
    assert body["sources"] == ["source.pdf, p.1"]


def test_the_passages_reach_the_model(grounded_client: TestClient) -> None:
    """The retrieved text must actually be in the prompt, not merely logged."""
    fake = FakeLLM()
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever(["المستقيم المقارب"])

    post(grounded_client, text="سؤال")

    assert "المستقيم المقارب" in fake.systems[0]


def test_nothing_retrieved_means_no_answer(grounded_client: TestClient) -> None:
    """The point of the whole phase: an empty search is a refusal, not a guess."""
    fake = FakeLLM(text="I would happily invent something here.")
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever([])

    body = post(grounded_client, text="سؤال عن شيء غير موجود").json()

    assert body["handled_by"] == "retrieval.empty"
    assert body["sources"] == []
    assert fake.calls == [], "the model was asked despite having no passages"


def test_a_broken_knowledge_base_does_not_fall_back_to_inventing(
    grounded_client: TestClient,
) -> None:
    fake = FakeLLM()
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever(fails=True)

    body = post(grounded_client, text="سؤال").json()

    assert body["handled_by"] == "retrieval.error"
    assert fake.calls == []


def test_a_missing_retriever_while_grounded_refuses(
    grounded_client: TestClient,
) -> None:
    fake = FakeLLM()
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = None

    body = post(grounded_client, text="سؤال").json()

    assert body["handled_by"] == "retrieval.unavailable"
    assert fake.calls == []


def test_commands_still_bypass_retrieval(grounded_client: TestClient) -> None:
    retriever = FakeRetriever(["irrelevant"])
    grounded_client.app.state.llm = FakeLLM()
    grounded_client.app.state.retriever = retriever

    assert post(grounded_client, text="/ping").json()["handled_by"] == "command.ping"
    assert retriever.queries == []


def test_healthz_distinguishes_disabled_from_unavailable(
    client: TestClient, grounded_client: TestClient
) -> None:
    assert client.get("/healthz").json()["retrieval"] == "disabled"
    grounded_client.app.state.retriever = None
    assert grounded_client.get("/healthz").json()["retrieval"] == "unavailable"


def test_the_reply_shows_what_the_citation_numbers_mean(
    grounded_client: TestClient,
) -> None:
    """A bare [1] tells the reader nothing, so it cannot be checked."""
    grounded_client.app.state.llm = FakeLLM(text="الجواب موجود في المادة [1].")
    grounded_client.app.state.retriever = FakeRetriever(["نص أول", "نص ثان"])

    reply = post(grounded_client, text="سؤال").json()["reply"]

    assert "[1] source.pdf, p.1" in reply
    # The model did not cite [2], so the reader is not sent to look it up.
    assert "[2] source.pdf, p.2" not in reply


def test_an_answer_citing_nothing_appends_nothing(grounded_client: TestClient) -> None:
    grounded_client.app.state.llm = FakeLLM(text="لا أجد ما يدعم هذا.")
    grounded_client.app.state.retriever = FakeRetriever(["نص"])

    reply = post(grounded_client, text="سؤال").json()["reply"]

    assert "المصادر" not in reply


# --- language drift ------------------------------------------------------------

ARTICLE_20 = "يطبق القانون السوري على كل سوري ارتكب جناية خارج سوريا [1]."
DRIFT = ARTICLE_20 + "即使以后失去或取得 Syrian 国籍也不影响此规定。"


def test_a_reply_that_drifts_language_is_asked_again(
    grounded_client: TestClient,
) -> None:
    """The real failure: a closing sentence in Chinese about Article 20."""
    fake = FakeLLM(replies=[DRIFT, ARTICLE_20])
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever(["المادة 20"])

    reply = post(grounded_client, text="سوري ارتكب جريمة خارج سوريا").json()["reply"]

    assert len(fake.calls) == 2
    assert "国" not in reply
    assert ARTICLE_20 in reply


def test_a_second_drift_loses_the_sentence_not_the_answer(
    grounded_client: TestClient,
) -> None:
    fake = FakeLLM(replies=[DRIFT, DRIFT])
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever(["المادة 20"])

    body = post(grounded_client, text="سوري ارتكب جريمة خارج سوريا").json()

    assert len(fake.calls) == 2
    assert "国" not in body["reply"]
    assert ARTICLE_20 in body["reply"]
    # The citation survived, so the source list did too.
    assert body["sources"] == ["source.pdf, p.1"]


def test_a_clean_reply_is_not_asked_twice(grounded_client: TestClient) -> None:
    fake = FakeLLM(text=ARTICLE_20)
    grounded_client.app.state.llm = fake
    grounded_client.app.state.retriever = FakeRetriever(["المادة 20"])

    post(grounded_client, text="سؤال")

    assert len(fake.calls) == 1


# --- Phase 9: conversation memory -----------------------------------------------


class FakeMemory:
    """An in-memory stand-in for ConversationStore."""

    def __init__(self, fails: bool = False) -> None:
        self.rows: dict[int, list] = {}
        self.fails = fails

    async def recent(self, chat_id: int, limit: int):
        if self.fails:
            raise RuntimeError("postgres is down")
        return self.rows.get(chat_id, [])[-limit:]

    async def append(self, chat_id: int, user_id: int, turns) -> None:
        if self.fails:
            raise RuntimeError("postgres is down")
        self.rows.setdefault(chat_id, []).extend(turns)

    async def clear(self, chat_id: int) -> int:
        return len(self.rows.pop(chat_id, []))


THEFT = "ما عقوبة السرقة؟"
ARMED = "وإذا كان السارق مسلحاً؟"


def _with_memory(client: TestClient, llm: FakeLLM, memory: FakeMemory | None = None):
    memory = memory or FakeMemory()
    client.app.state.llm = llm
    client.app.state.memory = memory
    retriever = FakeRetriever(["المادة 628"])
    client.app.state.retriever = retriever
    return memory, retriever


def test_a_follow_up_reaches_the_model_with_the_earlier_exchange(
    grounded_client: TestClient,
) -> None:
    fake = FakeLLM(replies=["الحبس سنة على الأقل [1].", "الحبس مع الشغل [1]."])
    _with_memory(grounded_client, fake)

    post(grounded_client, text=THEFT)
    post(grounded_client, text=ARMED)

    assert fake.histories[0] == []
    assert [(m.role, m.content) for m in fake.histories[1]] == [
        ("user", THEFT),
        ("assistant", "الحبس سنة على الأقل."),
    ]


def test_a_follow_up_is_searched_with_the_previous_question_as_context(
    grounded_client: TestClient,
) -> None:
    """`مسلحاً` alone may lose the word that matters; the previous question has it."""
    _, retriever = _with_memory(grounded_client, FakeLLM(text="جواب [1]."))

    post(grounded_client, text=THEFT)
    post(grounded_client, text=ARMED)

    assert retriever.queries == [THEFT, ARMED]
    assert retriever.contexts == [None, THEFT]


def test_remembered_answers_lose_their_citation_numbers(
    grounded_client: TestClient,
) -> None:
    """Last turn's [2] would point at this turn's passage 2."""
    memory, _ = _with_memory(grounded_client, FakeLLM(text="الحبس [1] والغرامة [2]."))

    post(grounded_client, text=THEFT)

    assert memory.rows[1][1].content == "الحبس والغرامة."


def test_a_refusal_is_remembered_as_a_refusal(grounded_client: TestClient) -> None:
    memory = FakeMemory()
    grounded_client.app.state.llm = FakeLLM()
    grounded_client.app.state.memory = memory
    grounded_client.app.state.retriever = FakeRetriever([])

    post(grounded_client, text="كيكة الشوكولا")

    assert [m.role for m in memory.rows[1]] == ["user", "assistant"]
    assert "لم أجد" in memory.rows[1][1].content


def test_a_failed_answer_is_not_remembered(grounded_client: TestClient) -> None:
    """Asking again after an error should look like asking the first time."""
    memory, _ = _with_memory(grounded_client, FakeLLM(fails=True))

    post(grounded_client, text=THEFT)

    assert memory.rows == {}


def test_commands_are_not_remembered(grounded_client: TestClient) -> None:
    memory, _ = _with_memory(grounded_client, FakeLLM())

    post(grounded_client, text="/ping")

    assert memory.rows == {}


def test_reset_forgets_the_conversation(grounded_client: TestClient) -> None:
    fake = FakeLLM(text="جواب [1].")
    memory, _ = _with_memory(grounded_client, fake)
    post(grounded_client, text=THEFT)

    body = post(grounded_client, text="/reset").json()
    post(grounded_client, text=ARMED)

    assert body["handled_by"] == "command.reset"
    assert fake.histories[-1] == []


def test_a_broken_memory_still_answers(grounded_client: TestClient) -> None:
    """Postgres down costs the follow-up its context, not the user an answer."""
    _with_memory(grounded_client, FakeLLM(text="جواب [1]."), FakeMemory(fails=True))

    body = post(grounded_client, text=THEFT).json()

    assert body["handled_by"] == "model.grounded"


def test_history_is_trimmed_oldest_first_and_starts_with_a_question() -> None:
    from app.api.responder import _fit_history
    from app.llm.base import Message

    history = [
        Message("user", "س" * 50),
        Message("assistant", "ج" * 50),
        Message("user", "س" * 30),
        Message("assistant", "ج" * 30),
    ]
    # 50 + 30 + 30 fits; the oldest question does not, so its answer - now
    # leading - goes too.
    assert [len(m.content) for m in _fit_history(history, 110)] == [30, 30]


def test_healthz_reports_memory(client: TestClient) -> None:
    assert client.get("/healthz").json()["memory"] == "disabled"


def test_an_answer_cut_off_by_the_token_limit_says_so(
    grounded_client: TestClient,
) -> None:
    """A legal list that stops halfway reads as complete unless it is marked."""
    from app.llm.client import LLMResult

    class CutOffLLM(FakeLLM):
        async def complete(self, system, user_message, max_tokens=1024, history=()):
            return LLMResult(
                text="- المادة 626 [1]\n-", model="m", input_tokens=1,
                output_tokens=1024, stop_reason="length",
            )

    _with_memory(grounded_client, CutOffLLM())

    reply = post(grounded_client, text=THEFT).json()["reply"]

    assert "انقطع الجواب" in reply


# --- Phase 12: rate limit -------------------------------------------------------


def test_too_many_messages_get_a_polite_refusal_not_silence(client: TestClient) -> None:
    from app.api.ratelimit import SlidingWindowLimiter

    client.app.state.limiter = SlidingWindowLimiter(2, 600)
    fake = FakeLLM()
    client.app.state.llm = fake

    bodies = [post(client, text=f"سؤال {i}").json() for i in range(3)]

    assert [b["handled_by"] for b in bodies[:2]] == ["model", "model"]
    assert bodies[2]["handled_by"] == "rate_limited"
    assert "دقيقة" in bodies[2]["reply"]
    # The limited message never reached the model.
    assert len(fake.calls) == 2


# --- Phase 13: errors carry a trace id, and /healthz checks for real -------------


def test_a_failure_in_telegram_can_be_found_in_the_log(client: TestClient) -> None:
    client.app.state.llm = FakeLLM(fails=True)
    response = post(client, text="سؤال")
    trace_id = response.headers["X-Trace-Id"]
    assert trace_id in response.json()["reply"]


def test_healthz_reports_an_unreachable_dependency(client: TestClient) -> None:
    class DownMemory:
        async def ping(self) -> bool:
            return False

    client.app.state.memory = DownMemory()
    assert client.get("/healthz").json()["memory"] == "unreachable"
