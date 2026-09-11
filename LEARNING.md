# Learning notes

Concepts as they are introduced, phase by phase. If you can explain each
entry in this file to another person, you understand the system.

---

## Phase 0 — Architecture

### The n8n / Python split

> **n8n owns the edges. Python owns the thinking.**

If it *connects two systems* → n8n. If it *decides, computes, or transforms
meaning* → Python.

The trap: n8n makes it so easy to add one more node that logic drifts onto the
canvas over months, until business rules live in an untestable JSON blob only
one person can edit. Draw the line early and hold it.

### LLM vs Workflow vs Agent

| | Definition | Decides what happens next? | Use when |
|---|---|---|---|
| **LLM call** | One prompt, one answer | No — you do | Fixed task: summarise, classify |
| **Workflow** | Fixed chain of steps, LLM calls inside | No — the graph does | The steps are always the same |
| **Agent** | LLM in a loop with tools; it picks tools until done | **Yes — the model does** | The right steps depend on the input |

Our assistant must handle "hi", "what's our refund policy?" and "what's the
deadline in that policy, and is it a business day?" — zero, one, and two-plus
tool calls. That variability is what an agent is for.

**But:** the model chooses which *tool*. It never chooses which *rule*.
Authorisation, token budgets, refusal-without-evidence and tool permissions
are code. The agent operates inside a cage built in Python.

### RAG in one paragraph

Before asking the LLM, search your own documents for the few most relevant
passages and paste them into the prompt. Solves three problems at once:
**knowledge** (the model has never seen your documents), **context limits**
(you cannot and should not stuff everything in), and **hallucination** (a
model asked a question it cannot answer produces fluent, confident, wrong
text).

### Vector, embedding, similarity

- An **embedding** turns text into a **vector** — a fixed-length list of
  floats representing "position in concept space".
- Similar meaning → nearby vectors. "refund" and "reimbursement" land close
  together even though they share no letters. That is what keyword search
  cannot do.
- **Cosine similarity** measures the *angle* between two vectors, ignoring
  their length. Typically reported 0–1; higher is more similar.
- Change the embedding model → every stored vector becomes meaningless and
  must be regenerated. Coordinates from two different maps cannot be compared.

### Chunking — the central tradeoff

- **Too small** (~100 tokens): precise but context-free. "It must be submitted
  within 30 days" — *what* must? The referent is in a chunk you did not fetch.
- **Too large** (~3000 tokens): contains your answer plus four unrelated
  topics. The embedding averages five meanings and matches nothing sharply.
- Prose sweet spot ≈ **400–800 tokens** (one to three paragraphs — the natural
  size of a self-contained idea), with **10–20% overlap** so a sentence on a
  boundary is not cut in half.
- These are a *starting hypothesis*, not a law. The right size is a property
  of your documents. **Method: print the chunks and read them.** If a chunk
  makes no sense to you out of context, it will not to the model either.

### top-k and why more is worse

Retrieve **wide** (~20 candidates — recall is cheap, and a chunk never
retrieved can never be used), then **narrow hard** (~5 after reranking).
More chunks in the prompt means more cost, more latency, and *worse* accuracy:
attention degrades over long contexts and irrelevant text actively distracts.

### Reranking

Vector search uses a **bi-encoder**: question and chunk are embedded
separately, so comparison is fast but coarse. A **cross-encoder** reranker
reads (question, chunk) *together* and scores relevance properly — slow but
accurate. Wide-then-narrow gets both.

### Retrieval failure

When the best similarity score is below threshold, the system must say so:

> "I couldn't find anything about that in the knowledge base."

Enforced in **Python code**, not requested in the system prompt. A prompt is a
suggestion; a code path is a guarantee. Making an assistant reliably admit
ignorance is harder than making it answer — and it is what separates a demo
from something a colleague can trust.

### MCP — and where it does not belong

The Model Context Protocol is a standard (JSON-RPC over stdio or HTTP) by
which an AI application discovers and calls capabilities from a separate
server. Three primitives: **tools** (actions the model invokes), **resources**
(data the model reads), **prompts** (templates the *user* invokes).

**MCP vs REST:** a REST API is designed for a *programmer* who reads docs and
writes a client. MCP is designed for a *model* that discovers capabilities at
runtime — schemas and descriptions are part of the protocol. That, not the
transport, is the real difference.

| Approach | Reusable by other AI clients? | Right when |
|---|---|---|
| Direct Python function | No | Logic is in our codebase and only we call it |
| REST API | By programmers | Humans/services consume it |
| n8n tool | Only inside n8n | The capability is an integration n8n already has |
| MCP server | **Yes, with self-describing schemas** | Multiple AI clients need it, or it is owned separately |

We do **not** wrap our own in-process retrieval in MCP (see ADR-006).

---

## Phase 1 — Development environment

### Virtual environment

One system Python, many projects with conflicting dependency versions. A venv
is a private folder with its own `pip` and `site-packages`; activating it puts
that folder first on `PATH`. Not magic, not a container — a path trick.

**The venv is never committed.** `requirements.txt` is the recipe; the venv is
the cooked meal. Anyone can recreate it from the recipe.

`requirements.txt` (runtime) is separate from `requirements-dev.txt` (tests,
linters) because production should not install a test framework.

### Configuration and secrets

Config is declared once, in `app/config.py`, with types. Nothing else in the
codebase reads `os.environ`. Three payoffs: **fail fast** (a missing value
raises at startup, not as `NoneType` mid-request), **one place to look**, and
**secret hygiene**.

- `.env` holds real values and is **gitignored**. Once a token reaches Git
  history it is compromised forever — rotate it, do not just delete the line.
- `.env.example` holds the same keys with empty values and **is** committed,
  so a new clone knows what exists without learning the values.
- `SecretStr` renders as `**********` if printed or logged by accident.
- Docker Compose reads the same `.env` for `${VARIABLE}` substitution — one
  source of truth for both Python and the containers.
- **Security defaults must be closed:** an empty Telegram allowlist denies
  everyone. A misconfiguration should lock you out, never let strangers in.

### Docker Compose

Describes several containers in one file. `up -d` starts them detached,
`down` removes them but keeps **named volumes**, `down -v` deletes the volumes
too — that is the command that wipes your database.

A **healthcheck** answers "is it *ready*?", not merely "is it running?".
Postgres accepts TCP connections seconds before it will accept queries.

### Container networking — the rule everyone gets wrong

| From | To | Address |
|---|---|---|
| Host (Python, browser) | container | `localhost:<published port>` |
| Container | container | **service name**, e.g. `http://qdrant:6333` |
| Container | host | `http://host.docker.internal:8000` |

Inside a container, `localhost` means *that container*. This is the single
most common cause of "connection refused" in this stack.

### Structured logging

A traditional log line is a sentence a human reads one at a time. A structured
line is data — filterable, countable, graphable:

```json
{"event": "retrieval.complete", "chunks": 5, "duration_ms": 240,
 "trace_id": "a1b2c3", "level": "info"}
```

Human-readable colours in development, JSON in production. Same call sites,
different renderer.

### Unit test vs integration test

`tests/test_config.py` touches no network and no container — it tests our own
logic and runs in milliseconds. That is a **unit test**.
`scripts/check_env.py` talks to real services — an **integration check**.
Keep them separate: unit tests must never fail because Docker is not running.
