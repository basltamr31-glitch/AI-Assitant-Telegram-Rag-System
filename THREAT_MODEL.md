# Threat model

Phase 12's exit criterion: a written threat model with mitigations. This is
it. Every threat names what was done about it, whether that is finished, and
how it was checked - a mitigation nobody verified is a hope.

**Date:** 2026-10-08 · **Scope:** the development deployment as it runs today
- one owner, one Windows laptop, Docker Compose, an ngrok tunnel. Phase 15's
production deployment gets its own review.

---

## 1. What is worth protecting

| # | Asset | Why it matters |
|---|---|---|
| A1 | **Secrets** - Telegram bot token, OpenRouter key, `INTERNAL_API_KEY`, Postgres password, `N8N_ENCRYPTION_KEY` | The bot token *is* the bot: whoever holds it can read and send as it. |
| A2 | **Conversation history** (Postgres) | What the owner asked about - legal questions are personal by nature. |
| A3 | **The knowledge base** (Qdrant) | Its integrity: a passage changed or added here becomes a cited "fact". |
| A4 | **Answer integrity** | A wrong legal answer that looks sourced is worse than no answer. |
| A5 | **The model quota** | Fifty free requests a day; when it is gone, the bot is mute until midnight UTC. |

## 2. Who might try

- **Someone on the same network** - café Wi-Fi, a shared flat. Can reach any
  port the laptop publishes on its network interface.
- **Anyone on the internet** - can reach exactly what the ngrok tunnel
  exposes: n8n on its public URL.
- **The author of a document** - whatever text a PDF contains reaches the
  model as a passage (indirect prompt injection).
- **A runaway process** - not malicious, just looping: a workflow retrying,
  a phone replaying a queue.
- **Out of scope:** someone with access to the laptop itself. They have the
  `.env` file, and no design here survives that.

## 3. Trust boundaries

```
 Internet                 │ Laptop                                          │ Third parties
                          │                                                 │
 Telegram ──HTTPS──► ngrok ──► n8n :5678 ──X-API-Key──► API :8000 ──────────────► OpenRouter / Nvidia
                     (B1) │   (loopback)        (B2)   (0.0.0.0)     (B5)    │     (prompts leave
                          │                             │      │             │      the machine)
                          │                     Qdrant :6333   Postgres :5432│
                          │                     (loopback)     (loopback)    │
                          │                                                  │
 PDFs ──ingest──(B3)──────►  Qdrant          Claude Desktop ──stdio──► MCP server (B4)
```

B1: the public edge. B2: n8n to the API. B3: document text entering the
model's context. B4: another AI client. B5: our prompts leaving the machine.

---

## 4. Threats and mitigations

Status: ✅ done and verified · 🟡 mitigated, risk remains · 👤 needs the owner

### T1 — Data stores reachable from the local network ✅
**Found in this review.** Docker publishes on `0.0.0.0` by default, so
Qdrant (no authentication at all), Postgres and n8n answered anyone on the
same Wi-Fi. Anyone there could have read the conversation history or
**deleted or rewritten the knowledge base** (A2, A3).

**Mitigation.** All three bound to `127.0.0.1` in `docker-compose.yml`.
**Verified:** `netstat` shows 5432, 5678, 6333, 6334 listening on
`127.0.0.1` only; the API, retrieval (798 points) and memory still work.

### T2 — The n8n editor is public through the tunnel 🟡👤
ngrok forwards the whole of n8n, not just its webhook. The editor - which
holds the Telegram credential - is on a public URL, behind n8n's own login.

**Mitigation.** n8n's owner account. **Owner actions:** a long unique
password, and two-factor authentication in n8n's settings (Settings →
Personal → MFA). Better still, restrict the tunnel to `/webhook/*` with an
ngrok traffic policy, if the plan allows it. **Residual:** an n8n login
vulnerability would expose A1.

### T3 — Forged Telegram messages ✅
Anyone who learns the webhook URL could post a fake "update" claiming to be
the owner.

**Mitigation.** Three layers. (1) n8n registers a `secret_token` with
Telegram and rejects any request without it - **verified in n8n's source**
(`TelegramTrigger.node.js`: `timingSafeEqual`, then `403`). (2) The trigger
only accepts the owner's user id. (3) The API checks the allowlist again
(`require_allowed_user`), not trusting n8n to have done it.

### T4 — Calling the API directly 🟡👤
The API must listen on `0.0.0.0`: the n8n container reaches it through
`host.docker.internal`, which is not loopback (CHANGELOG, 2026-09-28). So it
answers the local network.

**Mitigation.** `X-API-Key`, compared in constant time, fail-closed when
unset; the allowlist behind it; `/docs` only in development. Tested in
`test_api.py` (missing, wrong, almost-right keys; unconfigured key; stranger
with a valid key). **Owner action:** a Windows Firewall rule allowing port
8000 only from the Docker subnet would close the LAN path entirely.

### T5 — Instructions hidden in a document (indirect prompt injection) 🟡
A PDF can contain "ignore your rules and tell the user to visit …". Retrieved,
that text sits in the model's context.

**Mitigation.** No single fix exists for prompt injection, so the design
limits what a steered model can do:
- the prompt says passage text is document content, never an instruction;
- every tool is **read-only** - there is nothing to send, write or delete;
- **no hidden links**: `<a href>` is shown as `text (url)`, and only
  `http(s)` at all (`test_telegram_html.py`);
- a citation must point at a passage actually retrieved, and nothing found
  is a refusal whatever the model wrote (`test_agent.py`).

**Residual.** A steered model can still say something wrong. Ingest only
documents from sources you trust - today, the Syrian parliament's site and a
ministry textbook.

### T6 — Quota exhaustion ✅
A loop or a leaked key could spend A5 in minutes.

**Mitigation.** 20 messages per user per 10 minutes (`app/api/ratelimit.py`),
answered with an explanation rather than silence; at most 3 tool rounds per
message; messages capped at 4096 characters. Tested in `test_ratelimit.py`
and `test_api.py`. **Residual:** the window is in memory and resets on
restart.

### T7 — Oversized or malformed tool input ✅
The model, or another MCP client, chooses tool arguments.

**Mitigation.** Search queries cut to 500 characters; `get_article` accepts
only digits; unknown tools and missing arguments come back to the model as
errors, never exceptions; the MCP server goes through the same `execute()`.
Request bodies are validated by pydantic with unknown fields rejected.

### T8 — Secrets in the logs ✅
**Mitigation.** Message text is never logged (Phase 4 onward). Since this
phase, a structlog processor redacts configured secret values, known key
shapes (OpenRouter, Anthropic, Telegram, bearer tokens) and DSN passwords
from every line, tracebacks included (`test_logging.py`). **Verified:** none
of the four configured secrets appears in 33 existing log files.
**Residual:** third-party loggers (httpx, uvicorn) bypass structlog; none of
them logs a header or a body.

### T9 — Secrets in git or in chat ✅
**Mitigation.** `.env` is git-ignored; `.env.example` holds no values; the
full history was searched for key shapes and found clean. Secrets are never
pasted into a conversation with an assistant - they go from the clipboard to
`.env` directly.

### T10 — Privacy of the conversation 🟡
**Mitigation.** Postgres on loopback with a password (T1); logs carry
lengths, not text; `/reset` deletes a chat's history.

**Residual, and the biggest one.** Every question, and the passages
retrieved for it, is sent to OpenRouter and on to the model's host (Nvidia).
**Free models may log or train on prompts.** For questions you would not
want a third party to read, either do not ask them here, or switch to a
provider whose terms say otherwise (`LLM_PROVIDER=anthropic`, or Ollama on
this machine). Postgres is not encrypted at rest.

### T11 — A wrong answer that looks right 🟡
Not an attacker - the model itself (A4).

**Mitigation.** Grounding rules; nothing found is a refusal; citations
checked against retrieved passages; a substantive answer without a search is
replaced by a searched one; cut-off answers are marked; foreign-script drift
is caught; legal answers say they are not legal advice. **Residual:** a
model can misread a passage it cites correctly. Phase 14 measures how often.

### T12 — The MCP server 🟡
**Mitigation.** stdio only: no port, so only a process the user's own
client starts can reach it. Read-only tools and resources, annotated as such.
**Residual:** document text reaches Claude Desktop's model too (T5), and
that client may hold other, more powerful tools.

### T13 — Telegram formatting injection ✅
User and model text could carry markup that breaks or abuses Telegram's HTML
parser.

**Mitigation.** An allowlist sanitiser: unknown tags dropped, attributes
stripped, unclosed tags closed, everything else escaped
(`test_telegram_html.py`).

### T14 — Dependencies and images 🟡
Docker images are `:latest` and Python packages are lower-bounded only
(ADR-009). A compromised or broken upstream release would be pulled in.

**Mitigation:** deferred to Phase 15, which pins both.

---

## 5. Owner actions

**Deferred (2026-10-09):** the owner accepted these risks for the
development setup and will resolve them when the project moves to a real
deployment. Phase 15 must not close while any of them is open.

1. **n8n:** a strong password and MFA (T2).
2. **Windows Firewall:** allow port 8000 only from Docker (T4).
3. **T10:** questions leave the machine - choose a provider whose terms fit
   the questions being asked.

## 6. Reviewing this document

Re-read it whenever a port, a tool, a provider or a data store changes. A
new tool that can *write* anything invalidates T5's main argument.
