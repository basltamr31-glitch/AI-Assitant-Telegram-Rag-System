"""Tests for retrieval.

No network, no model, no Qdrant: the embedder and the store are fakes, because
what is being tested is the decision logic - what crosses the threshold, what
gets logged, how context is assembled. Whether the vectors are any good is a
question for Phase 14 and an eval set, not for a unit test.

The threshold tests carry the most weight. Phase 5 showed what this system does
without grounding: asked about Syrian contract law, it produced fluent, well
formed, entirely wrong Arabic with nothing to signal it was invented. Retrieval
that returns near-misses rather than nothing would feed exactly that answer,
with citations attached.
"""

from __future__ import annotations

import pytest

from app.rag.retrieval import Retrieved, Retriever
from app.rag.store import SearchResult


class FakeEmbedder:
    """Records what it was asked to embed, returns a fixed vector."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


class FakeStore:
    """Returns a scripted set of hits, and records the filters it was given."""

    def __init__(self, hits: list[SearchResult]) -> None:
        self.hits = hits
        self.calls: list[dict] = []

    def search(self, vector, *, limit, domain=None, source=None, score_threshold=0.0):
        self.calls.append(
            {"limit": limit, "domain": domain, "source": source,
             "threshold": score_threshold}
        )
        return [h for h in self.hits if h.score >= score_threshold][:limit]


def hit(score: float, page: int = 1, label: str = "") -> SearchResult:
    return SearchResult(
        text=f"نص القطعة في الصفحة {page}",
        score=score,
        source="12-sci-math-1.pdf",
        page=page,
        domain="curriculum",
        label=label,
    )


def make(hits: list[SearchResult]) -> tuple[Retriever, FakeEmbedder, FakeStore]:
    embedder, store = FakeEmbedder(), FakeStore(hits)
    return Retriever(embedder=embedder, store=store), embedder, store  # type: ignore[arg-type]


# --- the threshold -----------------------------------------------------------


def test_passages_below_the_threshold_are_not_returned() -> None:
    retriever, _, _ = make([hit(0.80), hit(0.50), hit(0.30), hit(0.10)])
    found = retriever.retrieve("سؤال", threshold=0.45)
    assert [round(r.score, 2) for r in found.results] == [0.80, 0.50]


def test_nothing_good_enough_returns_nothing_at_all() -> None:
    """The refusal is the feature. An empty result is a valid answer."""
    retriever, _, _ = make([hit(0.41), hit(0.33)])
    found = retriever.retrieve("سؤال", threshold=0.45)
    assert found.results == []
    assert found.found is False


def test_a_near_miss_is_kept_for_diagnosis() -> None:
    """"nothing above 0.45, best was 0.41" is a diagnosis; "no results" is not."""
    retriever, _, _ = make([hit(0.41), hit(0.33)])
    found = retriever.retrieve("سؤال", threshold=0.45)
    assert found.best_rejected_score == pytest.approx(0.41)
    assert len(found.rejected) == 2


def test_the_threshold_can_be_overridden_per_call() -> None:
    retriever, _, _ = make([hit(0.41)])
    assert retriever.retrieve("س", threshold=0.40).found is True
    assert retriever.retrieve("س", threshold=0.50).found is False


def test_an_empty_collection_is_not_an_error() -> None:
    retriever, _, _ = make([])
    found = retriever.retrieve("سؤال")
    assert found.found is False
    assert found.best_rejected_score is None


# --- the query --------------------------------------------------------------


def test_the_query_is_normalised_like_the_documents() -> None:
    """A question typed `إستخرج` must reach the index as `استخرج`."""
    retriever, embedder, _ = make([])
    retriever.retrieve("إستخرجْ النِّهاية")
    assert embedder.queries == ["استخرج النهاية"]


def test_filters_are_passed_through_to_the_store() -> None:
    retriever, _, store = make([])
    retriever.retrieve("س", domain="legal", source="law.pdf", top_k=3)
    assert store.calls[0]["domain"] == "legal"
    assert store.calls[0]["source"] == "law.pdf"
    assert store.calls[0]["limit"] == 3


def test_the_store_is_searched_without_a_score_filter() -> None:
    """The threshold is applied here, so near-misses survive to be inspected."""
    retriever, _, store = make([hit(0.9)])
    retriever.retrieve("س", threshold=0.8)
    assert store.calls[0]["threshold"] == 0.0


# --- context assembly --------------------------------------------------------


def test_context_numbers_every_passage_so_it_can_be_cited() -> None:
    retriever, _, _ = make([hit(0.9, page=62, label="تمرين 20"), hit(0.8, page=63)])
    context = retriever.retrieve("س", threshold=0.5).as_context()
    assert "[1] تمرين 20 (12-sci-math-1.pdf, p.62)" in context
    assert "[2] 12-sci-math-1.pdf, p.63" in context


def test_context_stops_at_a_passage_boundary() -> None:
    """Half an article read as a whole one is the error that matters here."""
    hits = [hit(0.9, page=n) for n in (1, 2, 3)]
    # Distinct texts, because an earlier version of this test used three
    # passages whose first twenty characters were identical and so passed
    # while asserting nothing.
    for n, h in enumerate(hits, start=1):
        object.__setattr__(h, "text", f"المقطع رقم {n} " + "ن" * 60)

    retriever, _, _ = make(hits)
    found = retriever.retrieve("س", threshold=0.5)
    context = found.as_context(max_chars=120)

    assert "[1]" in context
    for result in found.results:
        marker = f"المقطع رقم {result.page}"
        if marker in context:
            # Present at all means present whole.
            assert result.text in context


def test_context_of_nothing_is_empty() -> None:
    retriever, _, _ = make([hit(0.1)])
    assert retriever.retrieve("س", threshold=0.9).as_context() == ""


def test_citations_are_listed_separately() -> None:
    retriever, _, _ = make([hit(0.9, page=62, label="تمرين 20")])
    found = retriever.retrieve("س", threshold=0.5)
    assert found.citations() == ["تمرين 20 (12-sci-math-1.pdf, p.62)"]


def test_a_retrieved_result_can_always_name_its_source() -> None:
    """Without this a passage cannot be checked, which is the point of all of it."""
    retriever, _, _ = make([hit(0.9, page=7)])
    for result in retriever.retrieve("س", threshold=0.5).results:
        assert result.source and result.page
        assert str(result.page) in result.citation


# --- follow-ups (Phase 9) ------------------------------------------------------


class ScriptedStore(FakeStore):
    """Answers each search with the next scripted list of hits."""

    def __init__(self, *answers: list[SearchResult]) -> None:
        super().__init__([])
        self.answers = list(answers)

    def search(self, vector, *, limit, domain=None, source=None, score_threshold=0.0):
        self.calls.append({"limit": limit})
        return self.answers.pop(0)[:limit]


def test_with_context_both_searches_run_and_the_best_passages_win() -> None:
    """A follow-up is found with its context, a new topic without it."""
    embedder = FakeEmbedder()
    store = ScriptedStore(
        [hit(0.61, page=99), hit(0.50, page=53)],  # the follow-up alone
        [hit(0.69, page=99), hit(0.66, page=100)],  # with the previous question
    )
    retriever = Retriever(embedder=embedder, store=store)  # type: ignore[arg-type]

    found = retriever.retrieve("وإذا كان مسلحا؟", context="ما عقوبة السرقة؟", top_k=3)

    assert len(embedder.queries) == 2
    assert "السرقة" in embedder.queries[1] and "مسلحا" in embedder.queries[1]
    # Page 99 came back from both searches: once, at its better score.
    assert [(r.page, r.score) for r in found.results] == [(99, 0.69), (100, 0.66), (53, 0.50)]


def test_without_context_there_is_one_search() -> None:
    retriever, embedder, _ = make([hit(0.8)])
    retriever.retrieve("سؤال")
    assert len(embedder.queries) == 1
