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

### Working with documents (Phase 6-8)

Three steps, **run one at a time**. They are separate commands because two of
them are slow and only one of them is cheap to repeat.

```bash
# 1. Fetch the embedding model. Once, ~2.2 GB, resumable.
.venv\Scripts\python.exe scripts/fetch_model.py

# 2. Read a book into the OCR cache. Hours for a scanned book, and safe to
#    interrupt: every page is written as it is read and a re-run skips it.
.venv\Scripts\python.exe scripts/ocr_book.py material/your-book.pdf

# 3. Chunk, embed and store. Minutes. Re-run freely - this is the cheap half,
#    which is why it is separate from step 2.
.venv\Scripts\python.exe scripts/ingest.py your-book --domain curriculum

# Then look at what retrieval actually returns, before trusting it:
.venv\Scripts\python.exe scripts/search.py "سؤالك" --threshold 0
```

**Do not run these at the same time.** Steps 1 and 3 both want the embedding
model, and two processes downloading the same file end up with two partial
copies and no model - 1.2 GB of progress was lost that way. Step 3 also needs
step 2 to have finished, or it ingests whatever happens to be cached so far.

**Always `.venv\Scripts\python.exe`, never bare `python`.** The system
interpreter does not have this project's dependencies, and the failure is not
always a clean error: a script can get far enough to compete for a download
before it discovers what it is missing.

Once a corpus is ingested, set `RETRIEVAL_ENABLED=true` in `.env` and restart
the API. Until then the assistant answers ungrounded, which `/healthz` reports
as `retrieval: disabled`.

Services once running:

| Service | URL |
|---|---|
| n8n editor | http://localhost:5678 |
| n8n, publicly | `WEBHOOK_URL` in `.env` - the ngrok address Telegram calls |
| Internal API | http://localhost:8000 - `/healthz` needs no key |
| API docs | http://127.0.0.1:8000/docs (development only) |
| Qdrant dashboard | http://localhost:6333/dashboard |
| Postgres | localhost:5432 |

### The knowledge base over MCP (Phase 11)

The same documents, for other AI clients such as Claude Desktop. The server
is read-only: two tools (`search_knowledge_base`, `get_article`) and two
resources (`kb://documents`, `kb://article/{number}`). Qdrant must be running.

```powershell
# See it work, with no model involved: starts the server, lists what it
# offers, and calls a tool over stdio.
.venv\Scripts\python.exe scripts/mcp_client.py "عقوبة الرشوة"
.venv\Scripts\python.exe scripts/mcp_client.py --article 535
```

To add it to Claude Desktop, put this in `%APPDATA%\Claude\claude_desktop_config.json`
under `mcpServers`, with your own project path, and restart Claude Desktop:

```json
"telegram-assistant-kb": {
  "command": "C:\\path\\to\\project\\.venv\\Scripts\\python.exe",
  "args": ["-m", "app.mcp.server"],
  "cwd": "C:\\path\\to\\project"
}
```

The server finds `.env` by itself, wherever the client starts it from.

## Documentation

| File | Contents |
|---|---|
| `ARCHITECTURE.md` | System design, layers, data flow |
| `ROADMAP.md` | 16 phases, deliverables, status |
| `DECISIONS.md` | Architecture decision records - what and why |
| `THREAT_MODEL.md` | What is protected, from whom, and how - with owner actions |
| `LEARNING.md` | Concept glossary, phase by phase |
| `CHANGELOG.md` | What changed, when |

## Project status

Phases 0-11 complete. Milestones 1 and 2 reached: the
bot answers from the Syrian penal code with citations, remembers the
conversation, and decides for itself when to search. See `ROADMAP.md`.
