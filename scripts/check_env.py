"""Phase 1 acceptance test: is the development environment actually working?

Run this from the project root with the virtual environment active:

    python scripts/check_env.py

It verifies four things, independently, and tells you which one failed:

  1. Configuration loads and validates from .env
  2. Qdrant answers on its HTTP API
  3. Postgres accepts a connection and runs a query
  4. n8n serves its web UI

Testing each component on its own - rather than starting everything and
seeing whether "it works" - is the debugging habit this whole project is
built on. When something breaks in Phase 8, you will already know how to
find out *which* piece broke.
"""

from __future__ import annotations

import sys

import httpx
import psycopg
from qdrant_client import QdrantClient

from app.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.app_env == "production")
log = get_logger("check_env")

OK = "  [ OK ]"
FAIL = "  [FAIL]"


def check_config() -> bool:
    print("\n1. Configuration (.env -> Settings)")
    try:
        for key, value in settings.describe().items():
            print(f"       {key:<22} {value}")
        print(f"{OK} configuration loaded and validated")
        return True
    except Exception as exc:  # noqa: BLE001 - we want the reason, whatever it is
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        return False


def check_qdrant() -> bool:
    print(f"\n2. Qdrant  ({settings.qdrant_url})")
    try:
        client = QdrantClient(url=settings.qdrant_url, timeout=5)
        collections = client.get_collections().collections
        names = [c.name for c in collections] or ["(none yet - expected in Phase 1)"]
        print(f"       collections: {', '.join(names)}")
        print(f"{OK} Qdrant is reachable")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        print("       hint: is the container up?  docker compose ps")
        return False


def check_postgres() -> bool:
    print(f"\n3. Postgres  ({settings.postgres_host}:{settings.postgres_port}"
          f"/{settings.postgres_db})")
    try:
        with psycopg.connect(settings.postgres_dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                row = cur.fetchone()
        version = row[0].split(",")[0] if row else "unknown"
        print(f"       server: {version}")
        print(f"{OK} Postgres accepted a connection and ran a query")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        print("       hint: wrong password in .env means the volume was created")
        print("             with an older one -> docker compose down -v (wipes data)")
        return False


def check_n8n() -> bool:
    url = "http://localhost:5678"
    print(f"\n4. n8n  ({url})")
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        print(f"       HTTP {response.status_code} from {response.url}")
        print(f"{OK} n8n is serving its UI")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        print("       hint: n8n takes ~20s to boot. docker compose logs n8n")
        return False


def main() -> int:
    print("=" * 70)
    print("  Phase 1 environment check")
    print("=" * 70)

    results = {
        "config": check_config(),
        "qdrant": check_qdrant(),
        "postgres": check_postgres(),
        "n8n": check_n8n(),
    }

    print("\n" + "=" * 70)
    passed = sum(results.values())
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n  {passed}/{len(results)} checks passed")
    print("=" * 70)

    if passed == len(results):
        print("\n  Phase 1 environment is ready.\n")
        return 0
    print("\n  Fix the failing check(s) above before continuing.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
