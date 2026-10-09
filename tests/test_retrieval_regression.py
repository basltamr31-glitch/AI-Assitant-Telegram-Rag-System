"""Retrieval must not get worse than it was measured to be.

Phase 14 measured recall 90% and rejection 50% at the current settings. A
change to the chunking, the normalisation, the embedder or the threshold
that lowers either should fail loudly, not be discovered in a chat.

Opt-in, because it needs the real embedding model (~40 s to load) and a
running Qdrant:

    set RUN_EVALS=1 && .venv\\Scripts\\python.exe -m pytest tests/test_retrieval_regression.py
"""

from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.evals.retrieval import Ranked, load_cases, score

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EVALS") != "1", reason="set RUN_EVALS=1 to run against the real index"
)

# The floor, measured 2026-10-09 (top_k 10, threshold 0.50). Raise it when
# an improvement lands; never lower it to make a change pass.
MIN_RECALL = 0.90
MIN_REJECTION = 0.50


def test_retrieval_meets_its_measured_floor() -> None:
    from app.rag.retrieval import Retriever

    retriever = Retriever()
    cases = [c for c in load_cases() if c.kind in ("answerable", "unanswerable")]
    ranked = [
        Ranked(c, [(r.label, r.score) for r in retriever.retrieve(c.question, threshold=0.0).results])
        for c in cases
    ]
    result = score(ranked, get_settings().retrieval_score_threshold)
    assert result["recall"] >= MIN_RECALL, result
    assert result["rejection"] >= MIN_REJECTION, result
