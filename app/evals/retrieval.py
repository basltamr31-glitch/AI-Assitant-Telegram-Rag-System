"""Measure retrieval against questions whose right answer is known.

Why this exists
---------------
Every number the retrieval depends on was a guess until now. The threshold
of 0.45 says so in its own comment: "a deliberate guess, stated rather than
hidden. Phase 14 tunes it against an eval set." This is that eval set's
first use.

It runs without a language model. The question is narrower and comes first:
*did the passage that answers the question come back at all?* If it did not,
no model can answer from it, and a bad answer would be blamed on the wrong
part of the system.

What is measured
----------------
For questions the documents answer (`answerable`):

* **recall@k** - a gold article is among the passages kept. The one that
  matters most: it is what the model gets to read.
* **hit@1** - the best passage is a gold article.
* **MRR** - mean reciprocal rank: 1 for first place, 1/2 for second, 0 for
  missing. One number for "how high, on average".

For questions they do not answer (`unanswerable`):

* **rejection** - nothing at all passed the threshold. A passage that does
  pass is not wrong in itself, but it is the opening a model needs to
  answer from something merely related.

Raising the threshold buys rejection and costs recall; the sweep shows the
price of each step, which is what turns a guess into a choice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"


@dataclass
class Case:
    id: str
    kind: str
    question: str
    gold: list[str]
    note: str = ""


@dataclass
class Ranked:
    """What one search returned, before any threshold: (label, score) pairs."""

    case: Case
    hits: list[tuple[str, float]] = field(default_factory=list)

    def kept(self, threshold: float) -> list[tuple[str, float]]:
        return [(label, score) for label, score in self.hits if score >= threshold]

    def gold_rank(self, threshold: float) -> int | None:
        """1-based rank of the first gold article kept, or None."""
        for rank, (label, _) in enumerate(self.kept(threshold), start=1):
            if label in self.case.gold:
                return rank
        return None


def load_cases(path: Path | None = None) -> list[Case]:
    path = path or EVALS_DIR / "legal.jsonl"
    with path.open(encoding="utf-8") as f:
        return [Case(**json.loads(line)) for line in f if line.strip()]


def score(ranked: list[Ranked], threshold: float) -> dict[str, float]:
    """The metrics above, at one threshold."""
    answerable = [r for r in ranked if r.case.kind == "answerable"]
    unanswerable = [r for r in ranked if r.case.kind == "unanswerable"]
    ranks = [r.gold_rank(threshold) for r in answerable]
    n = len(answerable) or 1
    return {
        "threshold": threshold,
        "recall": sum(rank is not None for rank in ranks) / n,
        "hit@1": sum(rank == 1 for rank in ranks) / n,
        "mrr": sum(1 / rank for rank in ranks if rank) / n,
        "rejection": (
            sum(not r.kept(threshold) for r in unanswerable) / len(unanswerable)
            if unanswerable
            else 0.0
        ),
    }
