"""Turn text into vectors, locally.

ADR-003 chose local embeddings so that document text never leaves the machine.
ADR-016 narrowed the choice: the corpus is Arabic, so an English-only model is
not an option and a multilingual one is required.

`BAAI/bge-m3` is the default because it is the strongest open multilingual
retriever available to run on a laptop, and because its 8192-token window
suits legal articles, which are long and lose their meaning when cut. It costs
about 2.2 GB on disk and is downloaded once, on first use.

The interface is deliberately two methods
-----------------------------------------
`embed_documents` and `embed_query` exist separately because retrieval models
are trained asymmetrically: a passage and a question that should match are not
phrased alike, and models like E5 and BGE expect a prefix that says which role
the text is playing. Collapsing them into one `embed()` is the quiet mistake
that makes retrieval merely mediocre, with nothing in the logs to show why.

Changing the model invalidates everything
-----------------------------------------
Vectors from two models are not comparable, so swapping `EMBEDDING_MODEL`
means re-ingesting from scratch. The dimension is recorded in the Qdrant
collection, which turns a silent quality collapse into a loud mismatch.
"""

from __future__ import annotations

from functools import cached_property

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Models that were trained with instruction prefixes. Using the wrong prefix,
# or none, costs retrieval quality without raising anything.
_PREFIXES: dict[str, tuple[str, str]] = {
    # (document prefix, query prefix)
    "intfloat/multilingual-e5-base": ("passage: ", "query: "),
    "intfloat/multilingual-e5-large": ("passage: ", "query: "),
    "intfloat/multilingual-e5-small": ("passage: ", "query: "),
    # BGE-M3 needs no prefix: it was trained without one.
    "BAAI/bge-m3": ("", ""),
}


class LocalEmbedder:
    """A sentence-transformers model, loaded once and kept."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self.batch_size = settings.embedding_batch_size
        self._doc_prefix, self._query_prefix = _PREFIXES.get(self.model_name, ("", ""))

    @cached_property
    def _model(self):
        # Imported here rather than at module scope: sentence-transformers
        # pulls in torch, which takes seconds to import. Nothing that merely
        # mentions an embedder should pay that.
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        log.info("embedder.loading", model=self.model_name)
        model = SentenceTransformer(self.model_name, device="cpu")
        log.info(
            "embedder.ready",
            model=self.model_name,
            dimension=model.get_sentence_embedding_dimension(),
            max_seq_length=model.max_seq_length,
        )
        return model

    @property
    def dimension(self) -> int:
        """Vector size, which the Qdrant collection is created to match."""
        return self._model.get_sentence_embedding_dimension()

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []
        prepared = [prefix + t for t in texts] if prefix else texts
        vectors = self._model.encode(
            prepared,
            batch_size=self.batch_size,
            # Cosine similarity on unit vectors is a dot product, which is
            # what Qdrant is configured for.
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages that will be stored and searched over."""
        return self._encode(texts, self._doc_prefix)

    def embed_query(self, text: str) -> list[float]:
        """Embed a question. Not the same operation as embedding a passage."""
        return self._encode([text], self._query_prefix)[0]
