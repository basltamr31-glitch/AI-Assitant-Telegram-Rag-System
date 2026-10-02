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

**Date:** 2026-09-06 · **Status:** Accepted; amended by ADR-016, which
settles the model choice. The corpus is Arabic, so the English-only option
considered below is not available.

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

## ADR-016 — Ingestion for scanned Arabic books: vision OCR, a model chain, and a multilingual embedder

**Date:** 2026-10-02 · **Status:** Accepted (amends ADR-003)

**Context.** The first real corpus is `12-sci-math-1.pdf`, a 232-page Syrian
curriculum maths textbook, with Arabic legal material to follow. Inspecting it
settled several questions that had been open since Phase 0.

**What the book turned out to be.** Not a scan and not a born-digital PDF, but
a hybrid: produced in Adobe InDesign, with 6 pages carrying real text (the
copyright page, the contents) and 226 carrying only their page number as text
plus a 2421x3307 image of the page at roughly 300 dpi. Asking "is this PDF
scanned?" gives the wrong answer either way, so the loader asks per page. That
also makes the ordinary cases free: a born-digital legal PDF never reaches OCR.

---

### Decision 1 — OCR through a vision model, not a dedicated OCR engine

Tesseract with the Arabic pack is the obvious choice and was rejected. Arabic
is among the hardest scripts for classical OCR — letters join, each has up to
four contextual forms, and diacritics confuse segmentation — and this is a
*maths* textbook, where the equations matter as much as the prose. Tesseract
has no useful notion of a formula.

A vision model reads both at once. Measured on page 60: correct Arabic prose,
correct LaTeX for the limits and fractions, exercise numbering preserved, in
about 13 seconds at zero cost.

**Tradeoff.** A language model transcribing is a language model, and it
hallucinates where an OCR engine would simply produce nonsense. That is worse,
because nonsense is obvious and a plausible mistranslation is not. Decisions 3
and 4 exist because of it.

### Decision 2 — Free models, because ingestion is offline

ADR-015 set the rule: free where a retry costs only time, paid where latency
matters. Nobody waits for ingestion, so a `429` is an inconvenience rather
than a failure, and OCR is paid for once per book.

**Tradeoff, measured.** Free-tier rate limiting dominates: 24 pages took about
30 minutes, which extrapolates to roughly 5 hours for the book, against 8-13
seconds per page of actual model time. A few dollars of credit would cut that
to under an hour. The free path stays the default and `OCR_MODELS` is one line
in `.env`.

### Decision 3 — A chain of models, not one with retries

`dots-3-note-preview` emitted the Chinese word 结尾 on page 60 six times out of
six, then four out of four after a prompt change. That is a systematic failure,
and retrying a systematic failure is just waiting. The client walks a chain and
takes the first transcription that passes the quality gate; on that page it
fell through to `qwen`, absorbed two rate limits, and returned clean text.

### Decision 4 — A quality gate, because the failures are invisible

The models produced `لل-follow` where the page said `للتابع`, and `ل那麼` where
it said `ليكن`. Neither raises an error. Neither is visible in a
2000-character transcription. Both would be embedded, indexed, and then
silently fail to match anything for the life of the system — the worst class of
bug this project can have, because nothing ever reports it.

The gate rejects scripts that cannot belong in an Arabic corpus, and pages
that came back translated into English. The Arabic-ratio threshold is 10%,
because a page of exercises measures about 13%: it is mostly LaTeX, and the
letters in `\frac` and `\lim` are Latin. The foreign-script check is the
precise instrument; the ratio is a coarse net.

Pages that defeat every model are recorded in `failures.json` and skipped. One
unreadable page in 226 is a page to review by hand, not a reason to abandon a
book.

### Decision 5 — A versioned, resumable page cache

226 pages at 13 seconds is not work worth losing to a dropped connection — and
a dropped connection is exactly what ended the first run. Every page is written
to disk the moment it is read, keyed by the hash of the source document so a
resumed run skips even the image extraction, which costs 1.5 seconds a page.

The cache also records a `PIPELINE_VERSION`. Pages cached before `unwrap`
existed sat there as `\begin{tabular}` blobs and would never have been re-read;
a version bump turns that into a miss instead of a permanent corruption.

### Decision 6 — The embedder must be multilingual (this amends ADR-003)

ADR-003 left open the possibility of "a stronger, smaller English model".
The corpus is Arabic, so that option is gone. `BAAI/bge-m3` is the default:
the strongest open multilingual retriever that runs on a laptop, with an
8192-token window that suits legal articles, which are long and lose their
meaning when cut.

ADR-003's other commitments stand unchanged — embeddings are local, so
document text never leaves the machine, whichever chat model is answering.

**Tradeoff.** 2.2 GB on disk and slow CPU embedding. Acceptable at the stated
volume of about ten books at a time, and the alternative — a hosted embedding
API — would send every page of every document to a third party.

**Consequence.** Changing `EMBEDDING_MODEL` invalidates every stored vector.
The Qdrant collection records the dimension and the store refuses a mismatch,
so this surfaces as a loud error rather than as search that is quietly worse.

### Decision 7 — Chunk by domain; one collection with a filter

The curriculum's unit is a numbered exercise and law's unit is an article.
Both are what a person would cite, which is what makes a retrieved passage
checkable. Display mathematics is never split.

Both domains share one Qdrant collection behind an indexed `domain` filter
rather than one collection each. A collection per subject means two
configurations and two places to get the dimension wrong, for a filter Qdrant
applies cheaply — and it would fork the pipeline this project is explicitly
trying to keep as one template configured per corpus.

**Note on the chunk pattern.** It was wrong twice, and both times the output
looked reasonable. First it assumed `**20**` where the book writes `**20.**`,
matched nothing, and sent every page to the paragraph fallback. Loosened, it
then matched the `(1)` and `ا.` items *inside* an exercise, producing 111
chunks at a median of 110 characters — every sub-question severed from the stem
that gave it meaning. Neither failure announced itself. The lesson is recorded
here because it will recur with the legal corpus: chunk boundaries have to be
read, not assumed.

---

## ADR-015 — OpenRouter as the third provider; one model until evals say otherwise

**Date:** 2026-09-29 · **Status:** Accepted

**Decision.** `LLM_PROVIDER=openrouter` reaches hundreds of models through one
OpenAI-compatible endpoint and one key. `OPENROUTER_MODEL` selects which, with
no code change. The default is `qwen/qwen3.8-27b:free` — free, 27B, 262k
context. We run **one** model for now; per-task routing is deferred.

**Reason.** The knowledge base is Arabic legal and curriculum text, and the
local 1.7B model handles Arabic only roughly — one reply mixed a Vietnamese
word into an Arabic sentence. That matters more than it looks: from Phase 6
onwards every measurement is about retrieval quality, and a weak reader makes
a bad chunk and a bad answer indistinguishable. The reader has to be good
enough that retrieval is the thing being measured.

**Why now rather than after Phase 6.** Phase 6 itself needs no chat model —
ingestion uses the embedder only. But Phase 7 and 8 do, and the ingestion
choices made in Phase 6 get judged by them. Better to have the reader settled
before the thing it judges is built.

**Why not per-task routing yet.** The request was to route by task, using
different models for maths, legal and general questions. It is the right
eventual shape and it is deferred anyway, because a router needs a classifier
deciding which task a message is — a new component, a new failure mode, and no
evidence yet about which model is actually better at what. Phase 14 produces
that evidence. Routing built on measurement is a feature; routing built on
intuition is a guess with extra moving parts.

**Cost accounting.** OpenRouter prices change without notice, so `PRICING` is
not extended to cover it. Each call asks for `usage.include` and records the
cost the provider reports. `LLMResult.reported_cost_usd` takes precedence over
the table: a price list nobody updates looks authoritative and is wrong, which
is worse than having none.

**Alternatives considered.**
- *Stay on local Ollama* — free and private, but too weak in Arabic to trust
  as the reader while retrieval quality is being tuned. It stays one config
  line away and remains useful for offline work.
- *Provider SDKs for Gemini, Groq and others directly* — one adapter each, one
  key each. OpenRouter is a single adapter for all of them.
- *Pay for Claude again* — still the quality benchmark, and ADR-004's reasons
  stand. Revisit when Phase 14 can put a number on the gap.

**Tradeoffs.** A third party now sits between us and the model, seeing every
prompt. Conversations already leave the machine under any hosted provider, and
ADR-003 keeps documents local regardless — but the free tier is rate-limited
and a `:free` model id can be withdrawn, so `scripts/list_models.py` exists to
find the current options rather than trusting a list in a document.

---

## ADR-014 — The model provider is a config line; Ollama is the free path

**Date:** 2026-09-28 · **Status:** Accepted

**Decision.** `LLM_PROVIDER` in `.env` selects between `anthropic` (Claude,
paid) and `ollama` (a model running on this laptop, free). Development
defaults to `ollama` with `qwen3:1.7b`. `responder.py` depends on a Protocol
with one method and cannot name either provider.

**Reason.** The Anthropic account ran out of credit mid-phase: the API
answered `400 - Your credit balance is too low`. Work had to continue, and
ADR-004 had already promised that swapping the model would be "one config
line". This is the first time that promise was called in, and it held - the
change was a new adapter plus a factory, with nothing touched in n8n, the API
layer, or the responder's logic.

**Measured, on this machine.** `qwen3:1.7b` on an i7-1255U with no discrete
GPU: about 10 tokens/second once the model is warm, 1-5 seconds for a short
reply, and correct Arabic and arithmetic on simple questions. Usable.

**What it costs in quality.** A 1.7B model is not Opus 5, and the gap is
widest at the thing this project cares most about: ADR-004 chose Opus for
"reliably refusing to answer without evidence", which is exactly where small
models invent instead. Phase 8 depends on that behaviour and Phase 14 will
measure it rather than leave it a feeling. Expect the eval scores to make the
case for paying, at which point `LLM_PROVIDER=anthropic` switches back.

Occasional artefacts are visible already: one Arabic reply contained a
Vietnamese word mid-sentence.

**Alternatives considered.**
- *Buy Anthropic credits* - still the best quality, and the switch back is one
  line. Not chosen now because free had to work first.
- *A free hosted tier (Gemini, Groq)* - better quality than a 1.7B local
  model and still free, but it sends conversations to a third party and adds
  another account and key. Worth revisiting; the adapter makes it cheap to try.
- *A larger local model* - `qwen2.5:7b` would be noticeably better and would
  run at roughly 4-7 tokens/second here, i.e. 30-60 seconds per reply. Too
  slow for a chat bot on this hardware.

**Consequence.** Cost accounting has to distinguish "free" from "unpriced":
`LLMResult.local` returns a true zero instead of falling through to the
unknown-model warning, so Phase 14 compares real numbers.

**Unchanged.** ADR-003 already keeps embeddings local, so documents never
leave the machine regardless of which chat model answers.

---

## ADR-013 — Back to self-hosted n8n; the tunnel moves in front of it

**Date:** 2026-09-28 · **Status:** Accepted (supersedes ADR-011; reinstates ADR-010)

**Decision.** n8n runs in the `docker-compose.yml` container again, on
`localhost:5678`. The single ngrok tunnel now points at **n8n** instead of the
Python API, and n8n reaches the API over the Docker bridge at
`http://host.docker.internal:8000`.

**Reason.** The n8n Cloud trial ended. ADR-011 traded a weaker security
position for the n8n MCP connector, which made workflows programmatically
buildable — and that connector only ever reached the Cloud instance, so it
left with the subscription. The thing the trade bought is gone; the trade
reverses. ADR-010's original reasoning stands unchanged.

**What this improves.** The Python API is no longer on the public internet. It
was reachable by anyone who knew the ngrok hostname, guarded only by
`X-API-Key`. Now the tunnel terminates at n8n, and the API is reachable only
from this machine's LAN — one fewer layer of the system exposed, which is
exactly the argument ADR-010 made before ADR-011 overrode it.

**What it costs.**
- Workflows can no longer be built or edited through MCP. They are imported
  from `n8n/*.json` and edited by hand in the browser.
- Credentials do not migrate: they are encrypted per instance, so the Telegram
  token and the API key are re-entered once in the local n8n.
- `API_HOST` must be `0.0.0.0`, because `host.docker.internal` resolves to the
  host's bridge address, not to loopback. The API is therefore on the LAN. The
  `X-API-Key` check is what makes that acceptable, and Phase 15 removes the
  question entirely by putting both services on one private network.

**Migration cost, as predicted.** ADR-011 promised that leaving Cloud would be
"an import, not a rebuild", because every workflow is exported to `n8n/` in
Git. That held: `n8n/telegram-assistant.json` imports directly, with only the
API URL changed and the ngrok interstitial header removed.

**Consequence.** `WEBHOOK_URL` must be set to the public tunnel address.
Without it, n8n registers a `localhost` callback with Telegram, the webhook
saves successfully, and then never fires — a failure that looks like nothing
at all.

**Tradeoffs.** n8n's data now lives in a Docker volume on this laptop; if the
volume is deleted, the workflows come back from Git but the credentials do
not. Backups become a real concern, which is Phase 15's job.

---

## ADR-012 — ngrok with a reserved domain is the development tunnel

**Date:** 2026-09-19 · **Status:** Accepted; ADR-013 moved the tunnel in
front of n8n rather than the API. The reserved-domain reasoning below is
unchanged and is why the same domain kept working across that move.

**Decision.** n8n Cloud reaches the local FastAPI service through an ngrok
tunnel bound to a reserved (static) domain on the free tier. The API binds to
`127.0.0.1`; only ngrok connects to it, and only from this machine.

**Reason.** ADR-011 accepted that n8n Cloud would force the Python API to be
publicly reachable. This is where that bill comes due. Among the ways to pay
it, the deciding factor was not security — the three options expose the same
surface — but **URL stability**. A Cloudflare quick tunnel issues a new
hostname on every start, and the n8n HTTP Request node hardcodes its URL, so
every restart of uvicorn would mean editing the workflow. Phases 5 through 10
restart that server constantly. A reserved domain is configured once.

**Alternatives considered.**
- *Cloudflare quick tunnel* — no account at all, but a new URL each run. A
  named Cloudflare tunnel fixes that and needs a domain we do not own.
- *Revert to self-hosted n8n (ADR-010)* — genuinely the most secure: the
  tunnel would front n8n, and Python would never leave loopback. Rejected
  because it also removes the n8n MCP connector, which only reaches the Cloud
  instance and has been the main teaching accelerator. A security decision
  traded knowingly for tooling, not by accident.

**What makes the exposure acceptable.** Four independent things, none of which
is trusted alone:
1. `X-API-Key`, compared in constant time; an unset key fails closed.
2. The Telegram allowlist, enforced in n8n *and* again in Python.
3. `api_host = 127.0.0.1`, so nothing is served without the tunnel running.
4. `/docs` and `/openapi.json` are development-only, so a stranger gets no map.

**Tradeoffs.** An ngrok account and a process that must be running for the bot
to work — when the tunnel is down, the bot is down. The free tier also allows
one agent at a time. Phase 15 removes the tunnel entirely: the API and n8n
will sit on the same private network, and this ADR retires with it.

---

## ADR-011 — n8n Cloud during development, driven through MCP

**Date:** 2026-09-11 · **Status:** Accepted (supersedes ADR-010 for development)

**Decision.** Development uses the n8n Cloud instance at
`basltamr.app.n8n.cloud`, which Claude can build in directly through the n8n
MCP connector. The self-hosted container stays defined in
`docker-compose.yml` but is stopped; Phase 15 migrates back to it.

**Reason — new information.** ADR-010 was decided before an n8n MCP server
became available in the session. That connector changes what is possible:
Claude can now author, validate and version workflows programmatically, but
only against n8n Cloud — a cloud service cannot reach `localhost:5678`. This
turns workflow construction from a manual UI exercise into read-and-modify
learning: a working workflow to inspect, break and repair, which is faster
than assembling nodes in an unfamiliar tool.

Telegram also reaches n8n Cloud with no tunnel, so Phase 3 stays focused on
Telegram itself rather than on networking.

**Alternatives considered.**
- *Self-hosted (ADR-010)* — free indefinitely and identical to production,
  but every workflow is hand-built and Phase 3 needs a tunnel first.
- *Both in parallel* — deepest learning, roughly double the work.

**Tradeoffs.** Two real costs. (1) Phase 4 must expose the local Python API
to n8n Cloud through a tunnel, which is a weaker security position than
ADR-010's — mitigated by the `X-API-Key` header already in `config.py`, and
by binding the tunnel to a single port. (2) n8n Cloud is a time-limited trial,
then roughly €22/month. Workflows are exported to `n8n/*.json` in Git, so
migrating back to the container is an import, not a rebuild.

---

## ADR-010 — Self-hosted n8n, with a tunnel for inbound webhooks

**Date:** 2026-09-11 · **Status:** Superseded by ADR-011 for development;
still the target for production (Phase 15)

**Decision.** n8n runs as the container in `docker-compose.yml`, not on n8n
Cloud. Telegram reaches it through a Cloudflare tunnel introduced in Phase 3.

**Reason.** Telegram delivers updates only to a public HTTPS URL, and the
laptop has none — so exactly one tunnel is required either way. The question
is which side it sits on. Self-hosting puts the tunnel in front of **n8n**,
leaving the Python API bound to localhost and unreachable from the internet.
n8n Cloud would invert this: the public URL comes free, but the Python API
must then be exposed to the open internet for n8n to call it — a strictly
worse security position for a development machine. Self-hosting is also free
indefinitely and is what production will run, so development mirrors it.

**Alternatives considered.**
- *n8n Cloud* — frictionless in Phases 2–3, but a time-limited trial then
  roughly €20–24/month, and it forces the Python API to be publicly exposed.
- *Cloud now, migrate at Phase 4* — viable, since workflows export as JSON,
  but it means learning two deployment models to reach the same place.

**Tradeoffs.** One extra dependency (`cloudflared`) and one extra concept
(tunnels) in Phase 3. Both are needed for production regardless, so the cost
is paid once rather than avoided.

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
