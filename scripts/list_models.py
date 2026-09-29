"""Show what OpenRouter currently offers, cheapest first.

Model ids and prices change often enough that any list written into a
document is wrong within weeks. This asks the source. No API key needed - the
catalogue is public.

    python scripts/list_models.py           # free models
    python scripts/list_models.py --all     # everything, cheapest first
    python scripts/list_models.py qwen      # filter by name
"""

from __future__ import annotations

import sys

import httpx

CATALOGUE = "https://openrouter.ai/api/v1/models"


def per_million(model: dict) -> tuple[float, float] | None:
    try:
        p = model["pricing"]
        return float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6
    except (KeyError, TypeError, ValueError):
        return None


def main() -> None:
    args = [a for a in sys.argv[1:]]
    show_all = "--all" in args
    needles = [a.lower() for a in args if not a.startswith("--")]

    models = httpx.get(CATALOGUE, timeout=60).json()["data"]
    rows = []
    for m in models:
        price = per_million(m)
        if price is None:
            continue
        if needles and not any(n in m["id"].lower() for n in needles):
            continue
        if not show_all and not needles and price != (0.0, 0.0):
            continue
        rows.append((price[0] + price[1], m["id"], m.get("context_length") or 0, price))

    rows.sort(key=lambda r: (r[0], -r[2]))
    print(f"{'model id':<52} {'context':>9} {'$/M in':>8} {'$/M out':>8}")
    print("-" * 82)
    for _, mid, ctx, (pin, pout) in rows[:40]:
        print(f"{mid:<52} {ctx:>9,} {pin:>8.3f} {pout:>8.3f}")
    print(f"\n{len(rows)} shown. Set the one you want as OPENROUTER_MODEL in .env")


if __name__ == "__main__":
    main()
