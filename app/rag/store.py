"""Where the vectors live, and how they are searched.

ADR-002 put vectors in Qdrant and everything relational in Postgres. This is
the whole of our side of that: create a collection sized to the embedder,
upsert chunks, and search with a filter.

Two decisions worth stating
---------------------------
**The payload carries enough to cite.** A retrieved passage that cannot name
its source and page is unusable for the thing this project exists to do -
grounded answers you can check. Storing the text alongside the vector costs
disk and saves a second round trip to another store.

**`domain` is an indexed filter, not a separate collection.** The two bodies
of material - curriculum and law - share one collection with a `domain` field.
The alternative, a collection each, means two clients, two configurations and
two places to get the dimension wrong, for a filter Qdrant applies cheaply.
It also keeps the template shape the project is aiming at: one pipeline,
configured per corpus, rather than a fork per subject.

**Below the threshold, return nothing.** `ARCHITECTURE.md` already says it:
retrieval that returns its best three matches no matter how bad they are is
how a grounded assistant learns to invent. An empty result is a valid answer
and the refusal rules depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Iterable

from qdrant_client import QdrantClient, models

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.chunking import Chunk

log = get_logger(__name__)


@dataclass
class SearchResult:
    """One retrieved passage, with everything needed to cite and judge it."""

    text: str
    score: float
    source: str
    page: int
    domain: str
    label: str

    @property
    def citation(self) -> str:
        """How this passage refers to itself in an answer."""
        where = f"{self.source}, p.{self.page}"
        return f"{self.label} ({where})" if self.label else where


class VectorStore:
    """Qdrant, with the collection shaped to whatever embedder is configured."""

    def __init__(self, client: QdrantClient | None = None) -> None:
        settings = get_settings()
        self.collection = settings.qdrant_collection
        self._url = settings.qdrant_url
        self._injected = client

    @cached_property
    def _client(self) -> QdrantClient:
        """Connect on first use, not on construction.

        `QdrantClient(url=...)` performs a version handshake, so building one
        eagerly means the API cannot start - or even run its tests - while
        Qdrant is down. The service is still useful for commands with no
        vector store at all, so the connection belongs at the first query,
        where a failure is reportable, rather than at import time where it is
        a hang.
        """
        if self._injected is not None:
            return self._injected
        return QdrantClient(
            url=self._url,
            timeout=10,
            # The handshake warns about client/server version drift and costs
            # a round trip we would otherwise pay on every construction.
            check_compatibility=False,
        )

    def ensure_collection(self, dimension: int) -> None:
        """Create the collection if missing; refuse a dimension mismatch.

        A mismatch means the embedder changed, and vectors from two models are
        not comparable. Silently accepting them produces a collection that
        searches badly for reasons nothing in the logs would explain, so this
        raises instead.
        """
        existing = {c.name for c in self._client.get_collections().collections}

        if self.collection not in existing:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=dimension,
                    # Vectors are normalised by the embedder, so cosine is a
                    # dot product and the scores read as 0..1 similarity.
                    distance=models.Distance.COSINE,
                ),
            )
            # Filtering on an unindexed payload field works but scans; these
            # two are filtered on every query.
            for field in ("domain", "source"):
                self._client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            log.info("store.collection_created", name=self.collection, dim=dimension)
            return

        info = self._client.get_collection(self.collection)
        current = info.config.params.vectors.size
        if current != dimension:
            raise ValueError(
                f"collection '{self.collection}' holds {current}-dimensional "
                f"vectors but the embedder produces {dimension}. Changing the "
                "embedding model requires re-ingesting from scratch - delete "
                "the collection first."
            )

    def upsert(self, chunks: Iterable[Chunk], vectors: list[list[float]]) -> int:
        """Store chunks and their vectors. Re-ingesting updates, never duplicates."""
        chunks = list(chunks)
        if not chunks:
            return 0
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")

        points = [
            models.PointStruct(
                # The chunk's own id, derived from its content and position, so
                # a second ingestion of the same book overwrites rather than
                # doubling the collection.
                id=chunk.id,
                vector=vector,
                payload={
                    "text": chunk.text,
                    "source": chunk.source,
                    "page": chunk.page,
                    "domain": chunk.domain,
                    "label": chunk.label,
                    "content_hash": chunk.content_hash,
                    **chunk.metadata,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        self._client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def delete_source(self, source: str) -> None:
        """Remove every chunk belonging to one document.

        Chunk ids are derived from their text, so changing the chunking - or
        fixing the normalisation, which this project keeps doing - produces
        new ids. Without this, every re-ingestion would leave the previous
        version behind as orphans that still match searches and still cite
        themselves confidently.
        """
        self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source", match=models.MatchValue(value=source)
                        )
                    ]
                )
            ),
        )
        log.info("store.source_cleared", source=source)

    def search(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        domain: str | None = None,
        source: str | None = None,
        score_threshold: float = 0.0,
    ) -> list[SearchResult]:
        """Return the best matches above `score_threshold`, or nothing."""
        conditions = []
        if domain:
            conditions.append(
                models.FieldCondition(key="domain", match=models.MatchValue(value=domain))
            )
        if source:
            conditions.append(
                models.FieldCondition(key="source", match=models.MatchValue(value=source))
            )

        hits = self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=models.Filter(must=conditions) if conditions else None,
            score_threshold=score_threshold or None,
            with_payload=True,
        ).points

        return [
            SearchResult(
                text=hit.payload.get("text", ""),
                score=hit.score,
                source=hit.payload.get("source", ""),
                page=hit.payload.get("page", 0),
                domain=hit.payload.get("domain", ""),
                label=hit.payload.get("label", ""),
            )
            for hit in hits
        ]

    def by_label(self, label: str, *, domain: str | None = None) -> list[SearchResult]:
        """Every chunk with exactly this label, e.g. `المادة 535`.

        An exact lookup, not a search: "what does Article 535 say?" has one
        right answer, and a vector search for it returns whichever articles
        happen to *sound* like 535 - 533 and 534 score almost as well. No
        score applies, so 1.0 stands for "this is the passage asked for".

        `label` has no payload index, so this scans. At a few thousand
        chunks that is milliseconds; the day it is not, index it.
        """
        conditions = [models.FieldCondition(key="label", match=models.MatchValue(value=label))]
        if domain:
            conditions.append(
                models.FieldCondition(key="domain", match=models.MatchValue(value=domain))
            )
        points, _ = self._client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(must=conditions),
            limit=10,
            with_payload=True,
        )
        return [
            SearchResult(
                text=p.payload.get("text", ""),
                score=1.0,
                source=p.payload.get("source", ""),
                page=p.payload.get("page", 0),
                domain=p.payload.get("domain", ""),
                label=p.payload.get("label", ""),
            )
            for p in points
        ]

    def count(self, domain: str | None = None) -> int:
        """How many chunks are stored, optionally for one domain."""
        flt = None
        if domain:
            flt = models.Filter(
                must=[
                    models.FieldCondition(
                        key="domain", match=models.MatchValue(value=domain)
                    )
                ]
            )
        return self._client.count(
            collection_name=self.collection, count_filter=flt, exact=True
        ).count
