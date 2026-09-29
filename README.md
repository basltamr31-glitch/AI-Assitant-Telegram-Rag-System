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

# 4. Start infrastructure (Postgres, Qdrant and n8n - ADR-013)
docker compose up -d

# 5. Verify everything works
python scripts/check_env.py
```

### Running the assistant (from Phase 4)

Three processes, each in its own terminal. The bot is only alive while all
three are.

```bash
# a. The internal API. Two things this line is doing on purpose:
#    - .venv\Scripts\python.exe, not bare `python`: it needs no activation,
#      so the command cannot pick up the system interpreter by mistake.
#    - run_api.py, not `uvicorn` directly: the CLI does not read .env and
#      binds 127.0.0.1, which the n8n container cannot reach.
.venv\Scripts\python.exe scripts/run_api.py

# b. The tunnel that gives n8n a public HTTPS address for Telegram (ADR-013)
#    One-off setup:  scoop install ngrok
#                    ngrok config add-authtoken <token from the ngrok dashboard>
#    ngrok 3.39 takes --url, not the older --domain. Port 5678 is n8n, NOT
#    the API: since ADR-013 the tunnel fronts n8n, and the API stays off the
#    public internet.
ngrok http 5678 --url=$WEBHOOK_URL

# c. n8n itself runs in Docker from step 4. Just make sure the workflow is
#    activated in the editor at http://localhost:5678.
```

Services once running:

| Service | URL |
|---|---|
| n8n editor | http://localhost:5678 |
| n8n, publicly | `WEBHOOK_URL` in `.env` - the ngrok address Telegram calls |
| Internal API | http://localhost:8000 - `/healthz` needs no key |
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

Phase 5 of 15 complete — the bot converses. Milestone 1 reached.
See `ROADMAP.md`.
