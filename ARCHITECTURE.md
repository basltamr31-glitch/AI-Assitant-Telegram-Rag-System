# Architecture

## Guiding principle

> **n8n owns the edges. Python owns the thinking.**
>
> If it *connects two systems* -> n8n.
> If it *decides, computes, or transforms meaning* -> Python.

A second principle constrains the AI itself:

> **The model chooses which tool. It never chooses which rule.**
>
> Authorisation, token budgets, refusal-without-evidence and tool permissions
> are enforced in Python code, not requested in a prompt. A prompt is a
> suggestion; a code path is a guarantee.

## System diagram

```
                          +------------------+
                          |     Telegram     |   user sends
                          |    (Bot API)     |   "What's our refund policy?"
                          +--------+---------+
                                   | HTTPS webhook
                                   v
   +===============================================================+
   |                            n8n                                 |
   |   ORCHESTRATION - deterministic, no AI decisions               |
   |                                                                |
   |   [Telegram Trigger] -> [IF user_id in allowlist] -> [Normalize]
   |        -> [HTTP Request -> Python] -> [Telegram: Send Message] |
   |        -> [Error branch -> notify admin]                       |
   |                                                                |
   |   Separate workflows: [Cron 03:00 -> trigger ingestion]        |
   |                       [Cron -> health check -> alert]          |
   +===============================+================================+
                                   | POST /v1/chat
                                   | X-API-Key + JSON + trace_id
                                   v
   +===============================================================+
   |                   Python service - FastAPI                     |
   |   INTELLIGENCE                                                 |
   |                                                                |
   |   /v1/chat   /v1/ingest   /v1/health                           |
   |        |                                                       |
   |        v                                                       |
   |   +--------------------------------------------------+        |
   |   |                   AGENT LOOP                      |        |
   |   |  system prompt + history + tool schemas -> Claude |        |
   |   |  <- tool_use? -> execute -> feed back -> repeat   |        |
   |   +--+----------+------------+----------------+------+        |
   |      |          |            |                |               |
   |   RAG tool   Memory     Local tools      MCP client           |
   |      |          |            |                |               |
   +======+==========+============+================+===============+
          |          |            |                |
   +------v-----+    |            |         +------v-----------+
   |  Embedder  |    |            |         |   MCP Server     |
   |  (local)   |    |            |         |   own process    |
   +------+-----+    |            |         | tools/resources  |
          |          |            |         +------+-----------+
   +------v-----+  +-v---------+  |                |
   |   Qdrant   |  | Postgres  |  |                v
   | vectors +  |  | history   |  |         external services
   | metadata   |  | logs      |  +---------------->
   +------------+  +-----------+

   -- Ingestion (offline, separate path) --------------------------
   docs/ -> load -> parse -> clean -> chunk -> metadata -> embed -> Qdrant
```

## Layers

| # | Layer | Owns | Does NOT own |
|---|---|---|---|
| 1 | Telegram | Delivery, user identity (`chat_id`, `user_id`) | Anything about AI |
| 2 | n8n | Trigger, allowlist gate, normalisation, HTTP call, reply, cron, alerts | AI decisions |
| 3 | Python API | HTTP surface, request validation, orchestration of the agent | Transport concerns |
| 4 | LLM adapter | One call in, one typed response out; token + cost accounting | Business logic |
| 5 | Embedder | `embed(texts) -> vectors`, behind an interface | Which store the vectors go to |
| 6 | Vector DB | Vectors, payload text, metadata, filtered similarity search | Chunking policy |
| 7 | Ingestion | load -> parse -> clean -> chunk -> metadata -> embed -> store | Query-time behaviour |
| 8 | Retrieval | query processing, search, rerank, context assembly, refusal | Answer wording |
| 9 | Memory | conversation history in Postgres, windowing | Long-term facts (later) |
| 10 | Tools | Python functions + JSON schemas the model may call | Deciding permissions |
| 11 | MCP | Capabilities reusable by *other* AI clients, in a separate process | Our internal function calls |
| 12 | Storage | Postgres (relational), Qdrant (vectors), filesystem (sources) | - |
| 13 | Auth | Telegram allowlist, `X-API-Key` on n8n->Python, provider keys in `.env` | - |
| 14 | Logging | structlog JSON, `trace_id` threaded end to end, token/cost per call | - |
| 15 | Errors | retryable vs terminal classification, backoff, user-safe messages | - |
| 16 | Deployment | Docker Compose dev and prod; Caddy for HTTPS in prod | - |

## Networking model

| From | To | Address |
|---|---|---|
| Host (Python, browser) | any container | `localhost:<published port>` |
| Container | container | service name, e.g. `http://qdrant:6333` |
| Container (n8n) | host (Python API, dev) | `http://host.docker.internal:8000` |

Inside a container, `localhost` means *that container*. This is the single
most common source of "connection refused" in this stack.

## Request lifecycle (target state, Phase 10)

1. Telegram POSTs the message to n8n's webhook.
2. n8n checks `user_id` against the allowlist. Unknown -> drop, no reply.
3. n8n normalises to `{user_id, session_id, message, trace_id}` and POSTs to
   `/v1/chat` with the `X-API-Key` header.
4. Python loads the last N turns of history from Postgres.
5. The agent loop sends system prompt + history + tool schemas to Claude.
6. Claude either answers, or requests a tool (usually `search_knowledge_base`).
7. Retrieval embeds the query, searches Qdrant, reranks, and returns passages
   with scores. Below the score threshold it returns *nothing*, not junk.
8. The tool result goes back to Claude; the loop repeats until an answer.
9. Python persists both turns, logs tokens and cost against `trace_id`, and
   returns `{reply, sources[], trace_id}`.
10. n8n sends the reply to Telegram.
