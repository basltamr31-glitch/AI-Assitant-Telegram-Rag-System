"""Start the API on the host and port the configuration actually says.

Why not just `uvicorn app.api.main:app --reload`?
------------------------------------------------
Because the uvicorn CLI does not read `.env`. It binds `127.0.0.1` unless you
remember `--host`, and since ADR-013 that is the one address the n8n container
cannot reach: `host.docker.internal` resolves to the host's bridge address,
not to loopback. The bot then fails with a connection error that looks like a
firewall problem and is really a forgotten flag.

Settings are already typed and validated, so the fix is to let them decide.
One command, no flags to forget:

    python scripts/run_api.py
"""

from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    print(f"API      : http://{settings.api_host}:{settings.api_port}")
    print(f"provider : {settings.llm_provider}")
    if settings.api_host == "127.0.0.1":
        print("WARNING  : bound to loopback - the n8n container will NOT reach this.")
        print("           Set API_HOST=0.0.0.0 in .env (see ADR-013).")

    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        # Reload only in development; a production restart should be the
        # supervisor's decision, not a file watcher's.
        reload=settings.app_env == "development",
    )


if __name__ == "__main__":
    main()
