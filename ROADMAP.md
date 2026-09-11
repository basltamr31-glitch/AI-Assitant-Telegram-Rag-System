# Roadmap

Every phase ends with a review gate. Nothing starts before the previous phase
is explicitly approved.

**Legend:** ✅ done · 🔵 in progress · ⬜ not started

| Phase | Deliverable | Exit criterion | Status |
|---|---|---|---|
| **0** | Requirements, architecture, ADR-001…009 | Architecture approved | ✅ |
| **1** | venv, Git, structure, `.env`, typed config, `docker-compose.yml` | `check_env.py` passes 4/4 | 🔵 |
| **2** | n8n fundamentals: trigger, nodes, expressions, credentials, webhook | A manual webhook call returns your own JSON | ⬜ |
| **3** | Telegram bot via BotFather; n8n echoes messages | You message the bot, it echoes back — no Python yet | ⬜ |
| **4** | FastAPI service; n8n calls it; `X-API-Key` auth | 🏁 **MILESTONE 1** — full round trip Telegram→n8n→Python→Telegram | ⬜ |
| **5** | Claude integration, system prompt, token + cost logging | The bot converses. No memory, no knowledge | ⬜ |
| **6** | Ingestion: loaders → parse → clean → chunk → metadata → embed → store | You inspect your own chunks in the Qdrant dashboard | ⬜ |
| **7** | Retrieval + CLI test harness | Ask questions from the terminal, see chunks and scores | ⬜ |
| **8** | RAG wired into the chat endpoint | 🏁 **MILESTONE 2** — grounded answers with citations in Telegram | ⬜ |
| **9** | Conversation memory in Postgres; prompt caching | Follow-up questions work; survives a restart | ⬜ |
| **10** | Agent loop + tools; the model decides | Answers "hi" without searching; searches when it should | ⬜ |
| **11** | MCP server + client; second client (Claude Desktop) | A real tool call over MCP, end to end | ⬜ |
| **12** | Auth, validation, prompt injection, rate limits, secret-safe logging | A written threat model with mitigations | ⬜ |
| **13** | Retries, timeouts, fallbacks, structured errors, tracing | Kill Qdrant mid-conversation — the bot degrades gracefully | ⬜ |
| **14** | Eval dataset, retrieval metrics, LLM-as-judge, injection tests | A score you can improve against | ⬜ |
| **15** | Production Compose, HTTPS, backups, monitoring, pinned images | 🏁 **MILESTONE 3** — deployed and reachable | ⬜ |

## Milestones

**Milestone 1 — the round trip (end of Phase 4).** A message travels Telegram
→ n8n → Python → Telegram, authenticated and traced, with no AI at all.
Deliberate: once the plumbing is solid and observable, every later phase is
"swap in a smarter response generator" rather than "debug four systems at once".

**Milestone 2 — grounded answers (end of Phase 8).** The assistant answers
from your documents, cites sources, and says "I don't know" when the knowledge
base does not contain the answer.

**Milestone 3 — deployed (end of Phase 15).** Running on a server, over HTTPS,
with backups and monitoring.

## Open questions

Needed by Phase 6, not before:

1. **Subject matter** of the knowledge base — drives chunk sizing, the system
   prompt persona, and the refusal rules.
2. **Language(s)** of the documents — English-only allows a stronger, smaller
   English embedding model; anything else requires a multilingual model.
3. **Volume and location** — roughly how many documents, and where they live
   now (local folder, Google Drive, Notion, SharePoint, a website). If they
   live in a cloud service, n8n gains a genuine second job: scheduled sync.
