"""Find the passages that answer a question - or decide that none do.

The refusal is the feature
--------------------------
`ARCHITECTURE.md` states it as a rule: below the score threshold, retrieval
returns *nothing*, not its best guess. That is the whole difference between a
grounded assistant and a confident one.

It matters more here than in most RAG systems, for a reason this project has
already seen. In Phase 5, asked about Syrian contract law with no retrieval at
all, the model produced fluent, well-formed, entirely wrong Arabic. Nothing in
the answer signalled that it was invented. If retrieval hands back three
loosely-related passages whenever it finds nothing good, the model will weave
them into exactly the same kind of answer - and this time with citations
attached, which makes it worse rather than better.

So an empty result is a valid, expected outcome. The caller's job is to say
"I don't know", not to lower the bar until something comes back.

Query and document must be normalised alike
-------------------------------------------
A question typed `استخرج` has to match a page that was set as `إستخرج`. That
only works because both sides pass through `normalise()` - documents at
ingestion, queries here. Using it in one place and not the other is a bug that
shows up as mediocre retrieval and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.embedder import LocalEmbedder
from app.rag.normalise import normalise
from app.rag.store import SearchResult, VectorStore

log = get_logger(__name__)


@dataclass
class Retrieved:
    """What a search found, and what it is worth."""

    query: str
    results: list[SearchResult]
    threshold: float
    # Everything the search returned before the threshold was applied. Kept so
    # a near-miss can be inspected and logged: "nothing above 0.45, best was
    # 0.41" is a diagnosis, while "no results" is a mystery.
    rejected: list[SearchResult]

    @property
    def found(self) -> bool:
        return bool(self.results)

    @property
    def best_rejected_score(self) -> float | None:
        return max((r.score for r in self.rejected), default=None)

    def as_context(self, max_chars: int = 6000) -> str:
        """Format the passages for a prompt, each labelled so it can be cited.

        The numbering is what lets the model write "[1]" and the reader check
        it. A context block without stable labels produces answers whose
        sources cannot be traced, which is the failure this exists to prevent.
        """
        parts: list[str] = []
        used = 0
        for number, result in enumerate(self.results, start=1):
            block = f"[{number}] {result.citation}\n{result.text}"
            if used + len(block) > max_chars and parts:
                # Stop at a passage boundary rather than truncating one: half
                # an article read as whole is the error that matters here.
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    def citations(self) -> list[str]:
        return [r.citation for r in self.results]


class Retriever:
    """Embeds a question, searches, and applies the threshold."""

    def __init__(
        self,
        embedder: LocalEmbedder | None = None,
        store: VectorStore | None = None,
    ) -> None:
        settings = get_settings()
        self._embedder = embedder or LocalEmbedder()
        self._store = store or VectorStore()
        self._top_k = settings.retrieval_top_k
        self._threshold = settings.retrieval_score_threshold

    def retrieve(
        self,
        query: str,
        *,
        context: str | None = None,
        domain: str | None = None,
        source: str | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> Retrieved:
        """Search for `query`; with `context`, also for both together.

        `context` is the previous question in the conversation. A follow-up
        like "وإذا كان مسلحاً؟" is better found together with the question it
        follows: on the penal code that lifted the theft articles from 0.61
        to 0.69. A change of topic is the opposite case - "ما عقوبة القتل؟"
        after a theft question finds homicide alone at 0.69, and a theft-
        and-homicide mixture together at 0.61. Running both searches and
        keeping the best-scoring passages from either gets each case right
        without having to guess which one this is.
        """
        limit = top_k or self._top_k
        cutoff = self._threshold if threshold is None else threshold

        cleaned = normalise(query)
        queries = [cleaned]
        if context:
            queries.append(normalise(f"{context}\n{query}"))

        # Search without a score filter, then apply the threshold here, so the
        # near-misses are available to log rather than discarded by the store.
        searches = [
            self._store.search(
                self._embedder.embed_query(q),
                limit=limit,
                domain=domain,
                source=source,
                score_threshold=0.0,
            )
            for q in queries
        ]
        if len(searches) == 1:
            hits = searches[0]
        else:
            # The same passage usually comes back from both; keep it once, at
            # the better of its two scores.
            best: dict[tuple, SearchResult] = {}
            for hit in (h for found in searches for h in found):
                key = (hit.source, hit.page, hit.label, hit.text)
                if key not in best or hit.score > best[key].score:
                    best[key] = hit
            hits = sorted(best.values(), key=lambda h: h.score, reverse=True)[:limit]
        kept = [h for h in hits if h.score >= cutoff]
        rejected = [h for h in hits if h.score < cutoff]

        log.info(
            "retrieval.search",
            # The query itself is not logged, for the same reason message text
            # is not: logs travel, questions are private.
            query_chars=len(cleaned),
            with_context=bool(context),
            domain=domain,
            returned=len(kept),
            rejected=len(rejected),
            best_score=round(hits[0].score, 3) if hits else None,
            threshold=cutoff,
        )
        return Retrieved(
            query=cleaned, results=kept, threshold=cutoff, rejected=rejected
        )
