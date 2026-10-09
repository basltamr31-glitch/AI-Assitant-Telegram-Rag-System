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

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.config import get_settings
from app.core.errors import KnowledgeBaseUnavailable
from app.core.logging import get_logger
from app.rag.embedder import LocalEmbedder
from app.rag.normalise import normalise
from app.rag.store import SearchResult, VectorStore

log = get_logger(__name__)

T = TypeVar("T")


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


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# Phase 13: how long to stop trying Qdrant after it fails. A refused
# connection on Windows took 4.6 s to come back as an error, and an agent turn
# can search several times - so without this, one stopped container cost the
# user half a minute of waiting for the same answer each time.
BREAKER_COOLDOWN_S = 30.0


class Retriever:
    """Embeds a question, searches, and applies the threshold."""

    def __init__(
        self,
        embedder: LocalEmbedder | None = None,
        store: VectorStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        settings = get_settings()
        self._embedder = embedder or LocalEmbedder()
        self._store = store or VectorStore()
        self._top_k = settings.retrieval_top_k
        self._threshold = settings.retrieval_score_threshold
        self._clock = clock
        self._down_until = 0.0

    # --- Phase 13: failing fast -------------------------------------------------

    def _store_call(self, fn: Callable[[], T]) -> T:
        """Run one store operation behind a circuit breaker.

        Closed (the normal state): the call goes through. On any failure the
        breaker opens for `BREAKER_COOLDOWN_S`, and every call in that time
        fails at once with `KnowledgeBaseUnavailable`, without touching the
        network. After the cooldown the next call is tried for real - which
        is how the bot recovers from a restarted Qdrant with no restart of
        its own.
        """
        if self._clock() < self._down_until:
            raise KnowledgeBaseUnavailable("circuit open")
        started = self._clock()
        try:
            return fn()
        except Exception as exc:
            self._down_until = self._clock() + BREAKER_COOLDOWN_S
            log.error(
                "retrieval.store_failed",
                error=type(exc).__name__,
                after_ms=round((self._clock() - started) * 1000),
                cooldown_s=BREAKER_COOLDOWN_S,
            )
            raise KnowledgeBaseUnavailable(str(exc)) from exc

    @property
    def available(self) -> bool:
        """False while the breaker is open - for /healthz."""
        return self._clock() >= self._down_until

    def warm(self) -> None:
        """Load the embedding model now, not on the first question.

        Measured: 43 s to load bge-m3 on this laptop. Left to the first
        question after every restart, that plus the model's own 30 s ran
        past n8n's timeout and the user got nothing. Called in a background
        thread at startup.
        """
        started = self._clock()
        self._embedder.embed_query("warm up")
        log.info("retrieval.warm", seconds=round(self._clock() - started, 1))

    # --- lookups -----------------------------------------------------------------

    def documents(self) -> dict[str, int]:
        """What the knowledge base holds: source file -> passage count."""
        return self._store_call(self._store.documents)

    def article(self, number: str | int) -> Retrieved:
        """The article with this number, looked up exactly (Phase 10).

        Chunk labels were written with Western digits (`المادة 535`), so an
        Arabic-Indic `٥٣٥` from the user is translated before the lookup.
        """
        digits = str(number).strip().translate(_ARABIC_DIGITS)
        label = f"المادة {int(digits)}" if digits.isdigit() else ""
        results = (
            self._store_call(lambda: self._store.by_label(label, domain="legal"))
            if label
            else []
        )
        log.info("retrieval.article", label=label, returned=len(results))
        return Retrieved(query=label, results=results, threshold=1.0, rejected=[])

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
        started = self._clock()
        vectors = [self._embedder.embed_query(q) for q in queries]
        searches = [
            self._store_call(
                lambda v=v: self._store.search(
                    v, limit=limit, domain=domain, source=source, score_threshold=0.0
                )
            )
            for v in vectors
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
            duration_ms=round((self._clock() - started) * 1000),
        )
        return Retrieved(
            query=cleaned, results=kept, threshold=cutoff, rejected=rejected
        )
