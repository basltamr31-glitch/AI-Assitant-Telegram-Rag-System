# Architecture Decision Records

Each record states the decision, the reasoning, the alternatives that were
genuinely considered, and what the choice costs us. A decision without a
recorded tradeoff is a decision nobody can revisit later.

---

## ADR-001 — AI logic lives in Python, not in n8n

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** n8n handles triggering, routing, scheduling, retries and
integrations. All AI logic — agent loop, RAG, memory, tools — lives in a
Python service. n8n calls it over HTTP.

**Reason.** AI logic needs unit tests, readable diffs, step-through debugging
and an evaluation harness. Inside n8n it becomes one large JSON document that
cannot be meaningfully diffed, tested, or measured. The stated goal of this
project is understanding, and a node hides exactly what we want to learn.

**Alternatives considered.**
- *n8n AI Agent node* — working demo in about an hour, but untestable,
  unversionable in practice, and opaque at the point where retrieval quality
  is decided.
- *Everything in Python, no n8n* — fewer moving parts, but we then build a
  scheduler, retry logic and alerting ourselves, and learn no orchestration.

**Tradeoffs.** Slower to first demo. Two systems to run instead of one. In
exchange: testable logic, real debugging, and a measurable RAG pipeline.

---

## ADR-002 — Qdrant for vectors, PostgreSQL for everything else

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Qdrant (Docker) stores vectors, chunk text and metadata.
PostgreSQL 16 (Docker) stores conversations, users and logs.

**Reason.** Qdrant gives fast filtered similarity search and a web dashboard
that makes embeddings *visible* — directly valuable for learning. Postgres is
the correct home for relational data and will also back n8n in production.

**Alternatives considered.**
- *pgvector only* — one database, simpler backups, genuinely production-viable.
  Rejected for slightly weaker metadata filtering ergonomics and, decisively,
  no vector dashboard to learn from.
- *Chroma* — simplest to start, weakest in production.
- *Pinecone / Weaviate Cloud* — managed and paid; unnecessary at this scale.

**Tradeoffs.** One extra container, and two stores to back up instead of one.

---

## ADR-003 — Local embeddings via sentence-transformers

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Embeddings are generated locally. The embedder sits behind an
`embed(texts) -> vectors` interface.

**Reason.** Free, offline, private — no document text leaves the machine — and
instructive: the model, its dimensions and its behaviour are all inspectable.

**Alternatives considered.**
- *Hosted embedding API* — faster and typically higher retrieval quality, for
  a few cents per ingestion run, a second API key, and document text leaving
  the machine.

**Tradeoffs.** Slower ingestion on CPU (minutes, not seconds) and a few
hundred MB of model download. Reversible: the interface allows a swap, at the
cost of one full re-ingestion, since changing the embedder invalidates every
stored vector.

---

## ADR-004 — Claude Opus 5 as the LLM

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** `claude-opus-5` via the official `anthropic` SDK, behind an
adapter module.

**Reason.** 1M-token context and the strongest instruction-following of the
options priced for this workload — which matters most for the hardest
requirement in the project: reliably refusing to answer without evidence.

**Alternatives considered.** Sonnet 5 ($2/$10 per 1M tokens, ~2.5× cheaper);
Haiku 4.5 ($1/$5, cheapest, weaker at complex reasoning and rule-following);
a local model via Ollama (free and private, needs a GPU, noticeably more
hallucination on grounded QA).

**Tradeoffs.** $5/$25 per 1M tokens, roughly $0.035 per RAG message. At
development volume this is a few dollars a month. Prompt caching (Phase 9)
will cut the repeated system-prompt portion. The model ID is one config line.

---

## ADR-005 — Build LLM → workflow → agent, in that order

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Phase 5 ships a plain LLM call. Phases 7–8 ship a hardcoded RAG
workflow. Only Phase 10 introduces an agent loop with tool selection.

**Reason.** Each stage must be *felt* to be understood. Starting with an agent
means debugging tool selection, retrieval and prompting simultaneously, with
no known-good baseline to compare against.

**Alternatives considered.** Agent from the start — faster on paper, but every
bug becomes a four-variable problem.

**Tradeoffs.** Some code written in Phase 8 is refactored in Phase 10. That
refactor is the lesson, not waste.

---

## ADR-006 — MCP is used where it is reusable, not everywhere

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Our own agent calls its own retrieval code as a **direct Python
function**, not over MCP. We build **one** MCP server (Phase 11) exposing the
knowledge base as tools and resources, in its own process, usable by other
MCP clients such as Claude Desktop.

**Reason.** MCP's value is a standard interface across *different* AI clients
and process boundaries. Placing JSON-RPC between a function and its caller in
the same codebase adds a hop, a serialisation layer, a second process to
supervise and a new failure mode — solving a problem we do not have. Where it
does cross a boundary, MCP earns its place: reuse from other clients, a
permission boundary for credential-holding tools, and self-describing schemas.

**Alternatives considered.** Direct function (chosen internally); REST API
(designed for a programmer reading docs, not a model discovering capabilities
at runtime); n8n tool (only reusable inside n8n).

**Tradeoffs.** Two call paths to the knowledge base — in-process and MCP — so
the shared logic must stay in one module both can import.

---

## ADR-007 — Loader registry; document formats delivered in slices

**Date:** 2026-09-08 · **Status:** Accepted

**Decision.** Ingestion is built as a registry of loaders behind one interface,
`load(path) -> list[Document]`. Implementation order: Markdown/text and PDF
first, then DOCX/XLSX/PPTX, then HTML.

**Reason.** "Ingestion" is one pipeline per format, and the formats disagree:
Markdown headings give chunk boundaries and metadata for free; PDF extraction
mangles columns and tables and must be inspected, never trusted; spreadsheets
chunked as prose produce meaningless embeddings and need rows serialised with
their headers; HTML needs boilerplate stripping or nav bars poison every chunk.
Slicing by format keeps each new parser a single-variable problem.

**Consequence.** The metadata schema must be format-agnostic from the start —
`source_path`, `source_type`, `title`, `section`, `page_or_slide`,
`content_hash`, `ingested_at` — because changing it later forces a full
re-embedding.

**Tradeoffs.** All four formats are still delivered; only the order is staged.

---

## ADR-008 — n8n uses SQLite in development, Postgres in production

**Date:** 2026-09-08 · **Status:** Accepted

**Decision.** n8n keeps its default embedded SQLite database during
development. Phase 15 switches it to the Postgres container.

**Reason.** One less thing to configure and debug while learning n8n itself.
The switch is four environment variables and belongs with the other production
concerns (backups, HTTPS, monitoring).

**Tradeoffs.** Workflows are exported to `n8n/` in Git, so the development
database is never the source of truth and the migration is low risk.

---

## ADR-009 — Container images are unpinned in development

**Date:** 2026-09-08 · **Status:** Accepted, revisit in Phase 15

**Decision.** `postgres:16-alpine` is pinned by major version; Qdrant and n8n
use `:latest` during development.

**Reason.** Pinning to a tag that does not exist fails the very first
`docker compose up`. We pull what is current, then read the exact versions
back with `docker compose images` and pin those.

**Tradeoffs.** Until pinned, a later `docker compose pull` may change versions
underneath us. Phase 15's first task is to pin all three by digest.
