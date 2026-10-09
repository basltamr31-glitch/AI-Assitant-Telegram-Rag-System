"""Score retrieval on the eval set, and sweep the threshold.

    .venv\\Scripts\\python.exe scripts/eval_retrieval.py
    .venv\\Scripts\\python.exe scripts/eval_retrieval.py --show-misses

No model and no quota: one search per question, threshold 0, then every
threshold is scored offline from the same results. Writes
evals/results/retrieval-<date>.json so the next run has something to beat.
See app/evals/retrieval.py for what each number means.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from app.config import get_settings
from app.core.logging import configure_logging
from app.evals.retrieval import EVALS_DIR, Ranked, load_cases, score
from app.rag.retrieval import Retriever

SWEEP = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    configure_logging(level="WARNING", stream=sys.stderr)

    settings = get_settings()
    current = settings.retrieval_score_threshold
    cases = [c for c in load_cases() if c.kind in ("answerable", "unanswerable")]
    retriever = Retriever()

    ranked = []
    for case in cases:
        found = retriever.retrieve(case.question, threshold=0.0)
        ranked.append(Ranked(case, [(r.label, round(r.score, 3)) for r in found.results]))

    print(f"{len(cases)} questions, top {settings.retrieval_top_k}, current threshold {current}\n")
    print(f"{'threshold':>9} {'recall':>7} {'hit@1':>6} {'MRR':>6} {'rejection':>10}")
    sweep = [score(ranked, t) for t in SWEEP]
    for row in sweep:
        mark = "  <- current" if abs(row["threshold"] - current) < 1e-9 else ""
        print(
            f"{row['threshold']:>9.2f} {row['recall']:>7.0%} {row['hit@1']:>6.0%} "
            f"{row['mrr']:>6.2f} {row['rejection']:>10.0%}{mark}"
        )

    print("\nscore distribution at threshold 0:")
    for kind in ("answerable", "unanswerable"):
        best = sorted(r.hits[0][1] for r in ranked if r.case.kind == kind and r.hits)
        print(f"  {kind:<13} best score  min {best[0]:.3f}  median {best[len(best) // 2]:.3f}  max {best[-1]:.3f}")
    gold_scores = sorted(
        s for r in ranked if r.case.kind == "answerable" for label, s in r.hits if label in r.case.gold
    )
    if gold_scores:
        print(f"  gold passages  score       min {gold_scores[0]:.3f}  median {gold_scores[len(gold_scores) // 2]:.3f}")

    if args.show_misses:
        print(f"\nat threshold {current}:")
        for r in ranked:
            if r.case.kind == "answerable" and r.gold_rank(current) != 1:
                rank = r.gold_rank(current)
                print(f"  {r.case.id} gold {r.case.gold} rank {rank} | got {r.hits[:3]}")
            if r.case.kind == "unanswerable" and r.kept(current):
                print(f"  {r.case.id} should be empty | got {r.kept(current)[:3]}")

    out = EVALS_DIR / "results" / f"retrieval-{dt.date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "threshold": current,
                "top_k": settings.retrieval_top_k,
                "embedding_model": settings.embedding_model,
                "sweep": sweep,
                "cases": [{"id": r.case.id, "gold": r.case.gold, "hits": r.hits} for r in ranked],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\nwritten: {out.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
