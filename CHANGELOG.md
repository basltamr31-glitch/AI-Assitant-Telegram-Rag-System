# Changelog

All notable changes to this project. Newest first.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
