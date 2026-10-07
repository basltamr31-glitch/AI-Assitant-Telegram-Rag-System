# Changelog

All notable changes to this project. Newest first.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

---

## [Phase 12] — 2026-10-08 — Security, and a threat model (awaiting review)

**`THREAT_MODEL.md`** is the deliverable: assets, trust boundaries, fourteen
threats, what was done about each and how it was checked.

### Fixed
- **Qdrant, Postgres and n8n were reachable from the local network.** Docker
  published them on `0.0.0.0`, and Qdrant has no authentication: anyone on
  the same Wi-Fi could have read or deleted the knowledge base. All three
  are bound to `127.0.0.1`.

### Added
- A per-user rate limit, 20 messages per 10 minutes, answered with an
  explanation rather than silence (`app/api/ratelimit.py`).
- Log redaction: configured secrets, known key shapes and DSN passwords are
  removed from every structlog line, tracebacks included.
- 13 tests: the limiter, redaction, links, the query cap.

### Changed
- Links are never hidden: `<a href>` becomes `text (url)`, and only
  `http(s)` is shown at all - a model steered by a document cannot disguise
  a destination.
- The grounding prompt says passage text is document content, never an
  instruction.
- Tool search queries are cut at 500 characters; the MCP server now goes
  through the agent's `execute()`, so validation is shared.
- `API_VERSION` is `0.12.0`.

### Verified
- n8n's Telegram Trigger rejects requests without Telegram's secret token
  (read in its source: constant-time compare, then 403).
- None of the four configured secrets appears in 33 existing log files.

### For the owner
- MFA and a strong password on n8n, whose editor is public through the
  tunnel; a firewall rule for port 8000; and knowing that questions are sent
  to OpenRouter and Nvidia (THREAT_MODEL.md section 5).

---

## [Phase 11] — 2026-10-08 — The knowledge base over MCP

**ADR-019** records the choices; ADR-006 why the bot itself does not use MCP.

### Added
- `app/mcp/server.py` — an MCP server over stdio: tools
  `search_knowledge_base` and `get_article`, resources `kb://documents` and
  `kb://article/{number}`. Read-only.
- `scripts/mcp_client.py` — a client that starts the server as a separate
  process and calls a tool, printing each step.
- `Retriever.documents()` / `VectorStore.documents()` — a Qdrant facet: each
  source file and its passage count.
- 6 tests through a real MCP client, connected in-process.
- `mcp>=2.3,<3` in `requirements.txt`.

### Changed
- `configure_logging` takes a `stream`; the MCP server logs to stderr,
  because on stdio, stdout is the protocol. Colours are used only on a
  terminal, so redirected logs are plain text.

### Verified
- `scripts/mcp_client.py`: connected to the server subprocess, listed 2 tools
  and 2 resources, read `kb://documents` (754 + 44 passages), and a search
  returned Articles 626, 628, 622, 624 and 634.

---

## [Phase 10] — 2026-10-07 — The agent decides when to search

**ADR-018** records the design and the four rules.

### Added
- `app/agent/` — the loop and two tools, `search_knowledge_base` and
  `get_article` (exact lookup by article number, Arabic digits included).
- `Evidence`: every passage retrieved in a turn, numbered once, so `[n]`
  means the same thing across several tool calls.
- `OpenRouterClient.complete_with_tools()`, in OpenAI's tool-calling format.
- `AGENT_ENABLED` (true), `AGENT_MAX_ROUNDS` (3).
- 20 tests: each rule against a scripted model, and the wire format.

### Changed
- The grounding rules are one constant shared by the Phase 8 prompt and the
  agent's.
- Markdown quotes become `<blockquote>`, tables become one line per row, and
  `---` rules disappear: Telegram renders none of them.
- An OpenRouter reply with no `choices` is retried after 3 s, not at once:
  the cause was "Service temporarily overloaded", and one second later it
  still was.
- `API_VERSION` is `0.10.0`.

### Verified live
- "مرحبا" → answered, no search. "ما نص المادة 535؟" → `get_article`.
  A theft question → one search, five articles cited. Its follow-up → a
  search, then `get_article` for an article the results referred to.

---

## [Phase 9] — 2026-10-07 — Conversation memory

**ADR-017** records the design and what was measured.

### Added
- `app/memory/store.py` — a `messages` table in Postgres, keyed by Telegram
  chat. Created at startup if missing; `/healthz` reports `memory`.
- History reaches all three providers as real user/assistant turns
  (`Message` in `app/llm/base.py`).
- `/reset` forgets the current conversation.
- `MEMORY_ENABLED`, `MEMORY_MESSAGES` (8), `MEMORY_MAX_CHARS` (6000).
- `OPENROUTER_REASONING_EFFORT` (default `low`).
- 15 tests, two of them against the real Postgres to prove a conversation
  survives a new store - which is what a restart is.

### Changed
- A follow-up is searched with and without the previous question, and the
  best passages from either win (`Retriever.retrieve(context=...)`).
- `API_VERSION` is `0.9.0`.

### Fixed
- OpenRouter answers were cut off: Nemotron 3 Ultra spent its 1024-token
  budget mostly on hidden reasoning. The budget is 4096, and an answer that
  still hits it now says it was cut rather than reading as complete.

### Verified
- Live, on a test chat: theft at night, then "and if he was armed?", then -
  after restarting the API - "and in daytime?". The third answer used the
  first two (`memory.loaded messages=4` after the restart).

### Not done
- Prompt caching: deferred, ADR-017 Decision 3 says why.

---

## [Milestone 2] — 2026-10-07 — Grounded answers in Telegram

Phases 7 and 8 closed. Asked in Telegram about theft, homicide and crimes
committed abroad, the bot answers in Arabic from the penal code and lists
the articles it used.

### Changed
- The model is `nvidia/nemotron-3-ultra-550b-a55b:free`. The free tier of
  qwen3.8-27b was withdrawn; Nemotron 3 Super mixed French, Spanish and
  English words into its Arabic. Over ten grounded legal questions Ultra had
  no drift and cited `[n]` in nine. It takes 6-43 s, so the n8n HTTP node
  now waits 120 s.

### Fixed
- A reply in a foreign script (Chinese, Devanagari...) is retried once, then
  loses the offending sentences rather than reaching the user.
- An OpenRouter 200 with no `choices` is retried once instead of crashing
  on `KeyError`.
- Markdown `**bold**` and `*italic*` become Telegram HTML instead of showing
  their asterisks.

### Known limits
- The free tier allows 50 requests a day, shared by the bot and any testing.
- Latin-script drift is not caught, since Latin letters are legitimate in
  article names and LaTeX.
- "حكم الشروع في الجناية" retrieves the misdemeanour articles but not the
  general rule; the model declined rather than invent. Retrieval tuning is
  Phase 14.

---

## [Corpus] — 2026-10-06 — The penal code is in the knowledge base

### Fixed
- Boilerplate removal now catches a footer whose page counter changes
  (`... 45/121`). Only that trailing counter is masked: masking every digit
  made articles that differ only by number look like one repeated line.
- Articles split by a page break are rejoined (`join_continuations`). Before,
  the penal code had 52 chunks that were only a heading (`465المادة`) and 88
  bodies with no article number.
- Re-ingesting a document deletes its previous chunks first, so a change to
  the chunking no longer leaves orphans that still match searches.

### Measured
- `Syria-Penal-Cade-1949-Arabic.pdf`: 754 chunks, one per article plus the
  cover page; median 227 characters, no unlabeled bodies left.
- Theft, homicide and jurisdiction questions return the right articles at
  0.66–0.71. An unrelated question peaks at 0.39 and is refused.

---

## [Phase 8] — 2026-10-03 — Grounded answers (code complete, awaiting a corpus)

### Added
- `GROUNDED_SYSTEM_PROMPT` in `app/llm/prompts.py` — answer only from the
  passages, cite with `[n]`, and say plainly when the material does not cover
  the question. Separate rules for mathematics (the passages give the method;
  the reasoning is the model's, and the numbers from a worked example are not
  the user's numbers) and for law (quote the article, and say this is not
  legal advice).
- `ChatResponse.sources` — the citations, returned rather than only logged.
- `RETRIEVAL_ENABLED` — an operator's explicit switch back to ungrounded
  answering, for use while a corpus is still being built.
- 9 more API tests covering the grounded path.

### Changed
- `app/api/responder.py` retrieves on every non-command message, and the
  answer appends what each `[n]` refers to. A bare `[1]` cannot be checked,
  and checkability is the whole reason for retrieving.
- `app/rag/store.py` connects to Qdrant on first use rather than at
  construction. The eager version made the API hang at startup - and the test
  suite hang outright - whenever Qdrant was down.
- `API_VERSION` is `0.8.0`; `/healthz` distinguishes retrieval `ready`,
  `disabled` and `unavailable`.

### Notes
- **An empty search is a refusal, and a broken knowledge base is a refusal.**
  Neither falls back to the model's own knowledge. Phase 5 showed exactly what
  that fallback produces: fluent, confident, wrong Arabic about contract law,
  with nothing to signal it was invented.
- Retrieval is unconditional, so `مرحبا` retrieves nothing and is told so.
  That cost is ADR-005's staging, and the argument for the agent in Phase 10.

---

## [Phase 7] — 2026-10-03 — Retrieval, and a way to look at it

### Added
- `app/rag/retrieval.py` — normalises the query with the same function the
  documents went through, embeds it, searches, and applies the threshold.
- `scripts/search.py` — ask the knowledge base from the terminal and see the
  passages and their scores, with no model in the way rewriting the evidence
  into something plausible. `--threshold 0` shows the near-misses.
- `tests/test_retrieval.py` — 13 tests, all against fakes. What is tested is
  the decision logic; whether the vectors are good is Phase 14's question.

### Notes
- **Below the threshold, nothing is returned.** Phase 5 showed why: asked
  about Syrian contract law with no retrieval, the model produced fluent,
  well-formed, entirely wrong Arabic. Retrieval that hands back near-misses
  would feed exactly that answer, with citations attached - worse than no
  citations, because it looks checked.
- Near-misses are kept and reported rather than discarded, so "nothing above
  0.45, best was 0.41" is a diagnosis instead of a mystery.
- The threshold is 0.45 and that is a guess, stated rather than hidden. Phase
  14 tunes it against an eval set.

---

## [Phase 6] — 2026-10-02 — Ingestion for scanned Arabic books (in progress)

The first real corpus is a 232-page Syrian curriculum maths textbook, and
inspecting it settled questions that had been open since Phase 0. **ADR-016**
records all of it, including what was tried and rejected.

### Added
- `app/rag/loaders.py` — per-page decision between extractable text and an
  image for OCR. The book is a hybrid: 6 text pages, 226 images.
- `app/rag/ocr.py` — vision OCR with a model chain, a versioned resumable
  cache, retries for rate limits and network drops, and a quality gate.
- `app/rag/quality.py` — rejects foreign scripts and translated pages.
- `app/rag/normalise.py` — Arabic normalisation for documents and queries
  alike, preserving ta marbuta and alef maqsura.
- `app/rag/latex_unwrap.py` — recovers prose a model buried in `	ext{}`.
- `app/rag/chunking.py` — exercises for curriculum, articles for law,
  paragraphs as a fallback; display mathematics is never split.
- `app/rag/embedder.py` — local `BAAI/bge-m3`, with `embed_documents` and
  `embed_query` kept separate because retrieval models are asymmetric.
- `app/rag/store.py` — Qdrant, one collection with an indexed `domain` filter,
  refusing a dimension mismatch outright.
- `scripts/ocr_book.py` (slow, once per book) and `scripts/ingest.py` (fast,
  re-runnable whenever chunking changes).
- 50 new tests, every rejection case taken from output a model really produced.

### Changed
- **ADR-003 amended by ADR-016**: the corpus is Arabic, so the English-only
  embedding model it left open is not available.
- `material/` gitignored. GitHub refused the 118 MB textbook, which was the
  right answer to the wrong commit.

### Measured
- OCR: 8-13 seconds per page of model time, but free-tier rate limiting makes
  it ~5 hours for the book. Paid models would cut that to under an hour.
- Chunking: 44 chunks at a median of 522 characters, after two wrong patterns
  that both produced listings which looked perfectly reasonable.

---

## [ADR-015] — 2026-09-29 — OpenRouter, and cost the provider reports

### Added
- `app/llm/openrouter_client.py` — one OpenAI-compatible adapter reaching
  hundreds of models. `OPENROUTER_MODEL` switches between them with no code
  change, one step beyond what ADR-004 promised.
- `scripts/list_models.py` — prints OpenRouter's live catalogue, cheapest
  first. Model ids and prices change faster than documentation, so this asks
  the source instead of trusting a list.
- `LLMResult.reported_cost_usd` — the provider's own figure, which takes
  precedence over the `PRICING` table.
- Four tests: reported cost beats the table, a free model records a measured
  zero, the factory returns the right provider, and selecting OpenRouter with
  no key fails at startup rather than on the user's first message.

### Changed
- `strip_thinking()` moved to `app/llm/base.py`. Reasoning models are not an
  Ollama quirk and OpenRouter serves plenty of them.
- The default `LLM_PROVIDER` is now `ollama` rather than `anthropic`: the
  default should be the one that works without a bill.

### Notes
- Scope answered: the knowledge base is **Arabic**, covering both the Syrian
  curriculum and legal/real-estate material. Recorded in `ROADMAP.md`, along
  with the two design concerns it raises — retrieval can actively harm maths
  answers, and laws need article-level chunking that curriculum prose does not.

---

## [Fix] — 2026-09-28 — The API was being started on the wrong interface

`scripts/run_api.py` added, and the README now points at it. The documented
command was `uvicorn app.api.main:app --reload`, but the uvicorn CLI does not
read `.env`: it binds `127.0.0.1` unless `--host` is passed. Since ADR-013
that is precisely the address the n8n container cannot reach, because
`host.docker.internal` resolves to the host's bridge address rather than to
loopback. The script reads the typed settings instead, and warns if the
resulting bind would be unreachable from Docker.

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

## [Phase 5] — 2026-09-28 — A model behind the seam

### Verified
- The bot holds a conversation end to end, answering from `qwen3:1.7b`
  running locally at zero cost. `/ping` and the other commands still bypass
  the model entirely.

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
