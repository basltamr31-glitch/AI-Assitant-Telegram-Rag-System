# Telegram AI Assistant

A production-oriented AI assistant that answers questions from a private
knowledge base, delivered through Telegram.

**This is a learning project built to production habits.** The architecture is
deliberately explainable: every layer has one job, boundaries are plain HTTP +
JSON, and nothing is added without a recorded reason (see `DECISIONS.md`).

## What it does

```
You (Telegram) -> n8n -> Python API -> AI Agent -> answer with sources -> you
                                        |
                                        +-- RAG over your documents
                                        +-- conversation memory
                                        +-- tools / MCP
```

## Stack

| Layer | Choice | Why |
|---|---|---|
| Interface | Telegram Bot API | Free, universal, no UI to build |
| Orchestration | n8n (self-hosted) | Triggers, retries, schedules, integrations |
| Backend | Python 3.12 + FastAPI | All AI logic - testable and debuggable |
| LLM | Claude Opus 5 | 1M context, strong instruction-following |
| Embeddings | local `sentence-transformers` | Free, offline, private |
| Vector DB | Qdrant | Fast filtered search + inspectable dashboard |
| Database | PostgreSQL 16 | Conversations, users, logs |

## Quick start

```bash
# 1. Create and activate the virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell    (macOS/Linux: source .venv/bin/activate)

# 2. Install dependencies
pip install -r requirements-dev.txt

# 3. Configure
copy .env.example .env               # then fill in the blanks

# 4. Start infrastructure (Postgres + Qdrant; n8n runs on Cloud, see ADR-011)
docker compose up -d postgres qdrant

# 5. Verify everything works
python scripts/check_env.py
```

### Running the assistant (from Phase 4)

Three processes, each in its own terminal. The bot is only alive while all
three are.

```bash
# a. The internal API
uvicorn app.api.main:app --reload

# b. The tunnel that lets n8n Cloud reach it (ADR-012)
ngrok http 8000 --domain=<your-reserved-domain>.ngrok-free.app

# c. Nothing to run for n8n - it is Cloud-hosted. Just make sure the
#    workflow is Active.
```

Services once running:

| Service | URL |
|---|---|
| n8n | `N8N_BASE_URL` in `.env` (Cloud during development) |
| Internal API | http://127.0.0.1:8000 - `/healthz` needs no key |
| API docs | http://127.0.0.1:8000/docs (development only) |
| Qdrant dashboard | http://localhost:6333/dashboard |
| Postgres | localhost:5432 |

## Documentation

| File | Contents |
|---|---|
| `ARCHITECTURE.md` | System design, layers, data flow |
| `ROADMAP.md` | 16 phases, deliverables, status |
| `DECISIONS.md` | Architecture decision records - what and why |
| `LEARNING.md` | Concept glossary, phase by phase |
| `CHANGELOG.md` | What changed, when |

## Project status

Phase 4 of 15. See `ROADMAP.md`.
