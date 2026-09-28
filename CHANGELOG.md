# Changelog

All notable changes to this project. Newest first.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

---

## [ADR-014] — 2026-09-28 — A free model, and the adapter earns its keep

The Anthropic account ran out of credit (`400 - Your credit balance is too
low`), so the model provider became switchable.

### Added
- `app/llm/base.py` — `LLMResult` and `PRICING`, shared by every provider.
  `local=True` means a true zero cost rather than an unpriced unknown.
- `app/llm/ollama_client.py` — talks to a local Ollama server over httpx; no
  new dependency. Sends `think: false` **and** strips `<think>` blocks, since
  a reasoning model that ignored the flag would narrate its scratchpad into
  the chat.
- `create_llm_client()` in `app/llm/client.py`, selected by `LLM_PROVIDER`.
  Providers are imported lazily, so a missing Anthropic key is not a startup
  failure when Ollama is the one in use.
- Four tests: local cost is zero, no spurious pricing warning, think-blocks
  stripped, and the factory returns the configured provider.

### Changed
- `app/api/responder.py` depends on an `LLMProtocol` with a single method
  instead of naming `AnthropicClient`. It now cannot reference a vendor even
  by accident, and the test fake satisfies it without inheriting anything.

### Fixed
- The test fixture now pins `ANTHROPIC_API_KEY=""`. Two tests had started
  passing for the wrong reason once a real key appeared in the developer's
  `.env` — "does a missing key degrade gracefully?" had quietly stopped being
  tested. A unit test that reads `.env` is not a unit test.

---

## [ADR-013] — 2026-09-28 — n8n comes home

The n8n Cloud trial ended. ADR-011 had traded a weaker security position for
the n8n MCP connector; that connector only reached the Cloud instance, so it
left with the subscription. The trade reverses.

### Changed
- `docker-compose.yml` — the n8n service gains `WEBHOOK_URL`, `N8N_PROTOCOL`
  and `N8N_DEFAULT_BINARY_DATA_MODE=filesystem`. The `extra_hosts` entry for
  `host.docker.internal` was already there from Phase 1, written for exactly
  this case.
- `.env` — `N8N_BASE_URL` back to `http://localhost:5678`, new `WEBHOOK_URL`,
  and `API_HOST=0.0.0.0` so the container can reach the host.
- `n8n/telegram-assistant.json` — the live workflow, imported into the local
  n8n. Its API URL is now `http://host.docker.internal:8000/v1/chat`, and the
  `ngrok-skip-browser-warning` header is gone: that request no longer crosses
  the tunnel. The `phase-*.json` files stay as historical snapshots.

### Security
- **The Python API is off the public internet.** The tunnel terminates at n8n
  now, so the API is reachable only from this machine's LAN rather than by
  anyone who knew the ngrok hostname. This is the position ADR-010 argued for
  before ADR-011 overrode it.

### Fixed
- `check_env.py` passes **4/4** for the first time. Phase 1's exit criterion
  was never actually met while n8n lived in the cloud; it is now.

### Notes
- Credentials do not migrate between n8n instances — they are encrypted with
  each instance's own key — so the Telegram token and the API key are entered
  once more in the local editor.
- Workflows can no longer be built through MCP. They are imported from
  `n8n/*.json` and edited by hand.

---

## [Phase 5] — 2026-09-20 — Claude behind the seam (in progress)

### Added
- `app/llm/client.py` — the Anthropic adapter ADR-004 called for. Returns an
  `LLMResult` carrying text *and* token counts, with `cost_usd` computed from
  a `PRICING` table kept as data. Cost is measured from the first call, not
  added in Phase 14: a number you only start collecting once it hurts has no
  history to compare against.
- `app/llm/prompts.py` — the system prompt in its own module, so it can be
  diffed in review and pinned in an eval instead of hiding inside a call.
- `app/api/telegram_html.py` — a sanitiser for model output. Telegram accepts
  a short allowlist of tags and rejects the **whole message** on anything
  else, so unsanitised output means silence, not a formatting glitch. It keeps
  allowed tags, escapes the rest, drops dangerous attributes, and closes what
  the model left open.
- `tests/test_telegram_html.py` (11) and `tests/test_llm_client.py` (5).
- `anthropic>=0.40` in `requirements.txt`.

### Changed
- `app/api/responder.py` — the seam, rewritten. Commands (`/start`, `/help`,
  `/ping`, `/whoami`) are still answered locally and spend no tokens;
  everything else goes to Claude. A test asserts the model is never called for
  a command.
- `app/api/main.py` — builds the client once at startup and puts it on
  `app.state`, which also lets tests substitute a fake. `/healthz` now reports
  `llm: ready | unavailable`.
- `API_VERSION` is `0.5.0`.

### Notes
- A missing `ANTHROPIC_API_KEY` **degrades** rather than failing closed:
  commands keep working and chat explains what is wrong. That is the opposite
  of the `INTERNAL_API_KEY` rule, and deliberately so — one is a security
  control, the other is a capability.
- Truncation happens *before* sanitising. Cutting sanitised HTML could slice
  a tag in half; sanitising afterwards closes whatever the cut left open.
- Still no memory and no documents. The system prompt says so, so the
  assistant admits it instead of inventing a past.
- The prompt and the reply are not logged — only token counts and cost.

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
