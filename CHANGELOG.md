# Changelog

All notable changes to this project. Newest first.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

---

## [Phase 4] — 2026-09-20 — 🏁 Milestone 1: the round trip

### Verified
- Five consecutive webhook executions succeeded end to end, each completing
  in roughly 250–350 ms: phone → Telegram → n8n (EU) → public internet →
  ngrok → laptop → Python → back. Before publishing, the exact request n8n
  would send was replayed by hand through the public URL, so the only
  untested link at go-live was n8n itself.

### Added
- `app/api/main.py` — FastAPI app factory, `GET /healthz` (unauthenticated,
  so "is it up?" never needs the secret) and `POST /v1/chat` behind the key.
  Middleware stamps every request with a trace id, binds it to structlog's
  contextvars so nested log lines inherit it, and returns it as `X-Trace-Id`.
- `app/api/security.py` — `X-API-Key` compared with `secrets.compare_digest`,
  plus the second allowlist layer. An unset key fails closed, never open.
- `app/api/schemas.py` — typed request/response contract; `extra: forbid` so
  an unexpected field is a loud 422 rather than a silent drop.
- `app/api/responder.py` — deterministic replies (`/start`, `/help`, `/ping`,
  `/whoami`, echo). This is the seam Phase 5 replaces with Claude.
- `tests/test_api.py` — 22 tests, mostly about refusal: missing key, wrong
  key, key prefix, unconfigured key, stranger with a valid key, unknown
  fields, HTML injection.
- `fastapi` and `uvicorn[standard]` in `requirements.txt`.

### Changed
- `api_host` now defaults to `127.0.0.1` instead of `0.0.0.0`. The tunnel runs
  on this machine and can reach loopback; `0.0.0.0` would also have published
  the API to every device on whatever network the laptop joined.

### Added (n8n side)
- `n8n/phase-4-telegram-python.json` — Telegram Trigger → Extract Message →
  Call Assistant API → Send Reply. The HTTP Request node sends the API key as
  a Header Auth **credential**, so the exported JSON carries no secret.
- `ngrok-skip-browser-warning: true` on that request. Without it ngrok's free
  tier answers browser-ish callers with an HTML interstitial
  (`ERR_NGROK_6024`) instead of the JSON body — confirmed by hand before
  wiring it up.

### Notes
- `Extract Message` now passes the message text through **raw**. Python
  escapes it where it becomes HTML; escaping in both places would
  double-encode `<b>` into `&amp;lt;b&amp;gt;`. Escape once, at the point of use.
- The body is built with `JSON.stringify(...)` rather than concatenated by
  hand, so a message containing a quote or a newline cannot break it.
- `Send Reply` reads `chat_id` through `$('Extract Message').item.json`,
  because after the HTTP node `$json` is the API's response and no longer
  carries it.
- The message text is deliberately **not** logged — only its length. Logs get
  shipped and shared; the conversation is the private part.
- Interactive docs (`/docs`, `/openapi.json`) are served in development only.
  On a public tunnel they would hand a stranger a map of the service.

---

## [Phase 3] — 2026-09-19 — Telegram bot connected (in progress)

### Added
- `n8n/phase-3-telegram-echo.json` — Telegram Trigger → Extract Message →
  Send Reply. The bot echoes any message back with the sender's user ID,
  chat ID and message number.
- `n8n_base_url` in `app/config.py` — where n8n actually runs, typed like
  every other setting. Added to `.env` and `.env.example`.

### Changed
- `scripts/check_env.py` — the n8n check reads `settings.n8n_base_url`
  instead of a hardcoded `localhost:5678`, and probes `/rest/settings`
  (~1s) rather than `/`, which streams the whole editor SPA and timed out.

### Security
- The Telegram Trigger now carries `additionalFields.userIds`, so n8n drops
  updates from anyone but the owner before any downstream node runs. The same
  id is set in `TELEGRAM_ALLOWED_USER_IDS` for the Python side in Phase 4 —
  two independent layers, neither trusting the other.

### Notes
- The bot token is stored as an n8n **credential**, never in the workflow
  JSON, so `n8n/*.json` stays safe to commit. The JSON holds only the
  credential's id and name, which are references, not secrets.
- Updating a workflow through the API writes a new **draft** version and
  leaves `activeVersionId` untouched — the live bot keeps running the old
  version until the new one is published. Editing is not deploying.
- User text is HTML-escaped before it enters the reply, because the reply is
  sent with `parse_mode: HTML`. Output encoding, not input filtering.
- `appendAttribution` is off — otherwise n8n appends its own advert to every
  message the bot sends.

---

## [Phase 2] — 2026-09-11 — n8n fundamentals

### Added
- `n8n/phase-2-hello-webhook.json` — Webhook (`POST /hello`) → Build Reply →
  Respond to Webhook. Teaches triggers, the item model, expressions and the
  `=` prefix that separates an expression from a literal.

### Changed
- ADR-011 supersedes ADR-010 for development: n8n runs on n8n Cloud, driven
  through the n8n MCP connector. The container stays defined but stopped.

---

## [Phase 1] — 2026-09-08 — Development environment

### Added
- `docker-compose.yml` — PostgreSQL 16, Qdrant, n8n, with named volumes and a
  Postgres healthcheck. Reads `.env` for variable substitution.
- `app/config.py` — typed settings via `pydantic-settings`; secrets as
  `SecretStr`; derived `postgres_dsn`; allowlist parsing that denies by default.
- `app/core/logging.py` — `structlog` setup, console renderer in development,
  JSON in production.
- `scripts/check_env.py` — Phase 1 acceptance test: verifies config, Qdrant,
  Postgres and n8n independently.
- `tests/test_config.py` — unit tests for allowlist parsing, DSN assembly and
  secret redaction.
- `requirements.txt` / `requirements-dev.txt` — runtime vs development split.
- `.env.example` (committed) and `.env` (gitignored, secrets generated).
- `.gitignore` — excludes `.env`, `.venv/`, caches and `data/documents/*`.
- Package skeleton: `app/{api,core,llm,rag,memory,agent,mcp}`, `scripts/`,
  `tests/`, `data/documents/`, `n8n/`.

### Decisions
- ADR-007 — loader registry; document formats delivered in slices.
- ADR-008 — n8n on SQLite in development, Postgres in production.
- ADR-009 — container images unpinned in development, pinned in Phase 15.

---

## [Phase 0] — 2026-09-06 — Requirements and architecture

### Added
- `README.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `LEARNING.md`, `DECISIONS.md`,
  `CHANGELOG.md`.
- System architecture: three bands (Telegram / n8n / Python) with JSON-over-HTTP
  boundaries; sixteen layers with explicit ownership.
- 16-phase roadmap with three milestones.

### Decisions
- ADR-001 — AI logic in Python, not in n8n.
- ADR-002 — Qdrant for vectors, PostgreSQL for relational data.
- ADR-003 — local embeddings via `sentence-transformers`.
- ADR-004 — Claude Opus 5 as the LLM.
- ADR-005 — build LLM → workflow → agent, in that order.
- ADR-006 — MCP where it is reusable, not everywhere.
