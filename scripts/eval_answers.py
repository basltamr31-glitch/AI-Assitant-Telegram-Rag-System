"""Run the answer eval with the real model - in batches, resumable.

    .venv\\Scripts\\python.exe scripts/eval_answers.py --limit 10
    .venv\\Scripts\\python.exe scripts/eval_answers.py --kinds smalltalk,unanswerable
    .venv\\Scripts\\python.exe scripts/eval_answers.py --no-judge
    .venv\\Scripts\\python.exe scripts/eval_answers.py --summary

Spends real quota: about two requests per question and one per judgement,
~110 for the whole set. Each result is appended to
evals/results/answers-<run>.jsonl as soon as it exists, a rerun of the same
run skips what is done, and a spent daily quota stops the run cleanly. The
run name is derived from the model and the retrieval settings, so changing
any of them starts a fresh run instead of mixing two configurations.

See app/evals/answers.py for what is checked and why.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from app.api.responder import respond
from app.api.schemas import ChatRequest
from app.config import get_settings
from app.core.errors import QuotaExhausted
from app.core.logging import configure_logging
from app.evals.answers import (
    RecordingRetriever,
    check,
    declined_request,
    faithful_request,
    parse_verdict,
    summarise,
)
from app.evals.retrieval import EVALS_DIR, load_cases
from app.llm.client import create_llm_client
from app.rag.retrieval import Retriever

# Free upstreams throttle bursts with 429s; a pause between questions costs
# minutes over the whole set and saves the retries.
PAUSE_S = 5


def run_name() -> str:
    s = get_settings()
    model = s.openrouter_model.split("/")[-1].replace(":", "-")
    return f"{model}-t{s.retrieval_score_threshold}-k{s.retrieval_top_k}"


async def judge(llm, system: str, user: str) -> dict:
    result = await llm.complete(system=system, user_message=user, max_tokens=2048)
    return parse_verdict(result.text)


async def main(args) -> int:
    path = EVALS_DIR / "results" / f"answers-{run_name()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    done = []
    if path.exists():
        done = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    if args.summary:
        print(f"run {run_name()}: {len(done)} results")
        for kind, line in summarise(done).items():
            print(f"  {kind:<19} {line}")
        return 0

    kinds = set(args.kinds.split(",")) if args.kinds else None
    todo = [
        c for c in load_cases()
        if c.id not in {d["id"] for d in done} and (kinds is None or c.kind in kinds)
    ][: args.limit]
    print(f"run {run_name()}: {len(done)} done, {len(todo)} to go -> {path.name}")

    llm = create_llm_client()
    base = Retriever()
    for i, case in enumerate(todo, start=1):
        retriever = RecordingRetriever(base, plant=case.kind == "injection_document")
        request = ChatRequest(chat_id=-990000, user_id=-990000, text=case.question)
        started = time.perf_counter()
        try:
            reply, handled_by, sources = await respond(request, llm, retriever, agent=True)
            if handled_by == "model.quota":
                raise QuotaExhausted("during the turn")
            judged = None
            if not args.no_judge and case.kind == "answerable" and handled_by == "agent.grounded":
                judged = await judge(llm, *faithful_request(case.question, retriever.passages, reply))
            elif not args.no_judge and case.kind == "unanswerable" and handled_by != "agent.nothing_found":
                judged = await judge(llm, *declined_request(case.question, reply))
            elif case.kind == "unanswerable":
                judged = {"declined": True, "by": "code"}
        except QuotaExhausted:
            print("\nthe daily quota is spent - progress is saved; run again after it resets.")
            break
        outcome = {
            "id": case.id,
            "kind": case.kind,
            "handled_by": handled_by,
            "passed": check(case.kind, case.gold, handled_by, sources, reply),
            "sources": sources,
            "reply": reply,
            "judged": judged,
            "seconds": round(time.perf_counter() - started, 1),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        done.append(outcome)
        verdict = {True: "pass", False: "FAIL", None: "-"}[outcome["passed"]]
        print(f"[{i}/{len(todo)}] {case.id} {case.kind:<18} {handled_by:<20} {verdict:<4} "
              f"judge={judged} {outcome['seconds']}s")
        if i < len(todo):
            await asyncio.sleep(PAUSE_S)

    print()
    for kind, line in summarise(done).items():
        print(f"  {kind:<19} {line}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--kinds", help="comma-separated, e.g. smalltalk,unanswerable")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--summary", action="store_true", help="print results so far, spend nothing")
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    configure_logging(level="WARNING", stream=sys.stderr)
    sys.exit(asyncio.run(main(parser.parse_args())))
