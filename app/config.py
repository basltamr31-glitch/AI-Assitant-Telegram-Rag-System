"""Typed application configuration, loaded once from the environment.

Why this module exists
----------------------
Every setting the application needs is declared here, with a type. Nothing
reads `os.environ` anywhere else in the codebase. That gives us three things:

1. **Fail fast.** A missing or malformed value raises at startup with a clear
   message, instead of surfacing as `NoneType` deep inside a request.
2. **One place to look.** "What can be configured?" has exactly one answer.
3. **Secret hygiene.** Secrets use `SecretStr`, which renders as `**********`
   if it is ever printed or logged by accident.

Usage
-----
    from app.config import get_settings

    settings = get_settings()
    print(settings.qdrant_url)
    print(settings.anthropic_api_key.get_secret_value())   # explicit unwrap
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration for the assistant, validated at load time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore variables in .env that we have not declared here (e.g. the
        # ones only docker-compose cares about). Without this, pydantic would
        # reject the whole file.
        extra="ignore",
        # Pydantic reserves the `model_` prefix for its own methods. We have a
        # field called `anthropic_model`, so we switch that protection off.
        protected_namespaces=(),
    )

    # --- Application ---------------------------------------------------------
    app_env: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Safe default. ADR-013 makes development override this to 0.0.0.0 in
    # .env, because the n8n container reaches the host through
    # host.docker.internal, which is not loopback. The API is then on the
    # LAN but no longer on the internet: the tunnel fronts n8n instead.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # --- Internal auth (n8n -> Python), used from Phase 4 --------------------
    internal_api_key: SecretStr = SecretStr("")

    # --- Which model answers (ADR-014) ---------------------------------------
    # The one line that decides whether a reply costs money or CPU time.
    llm_provider: Literal["anthropic", "ollama", "openrouter"] = "ollama"

    # --- OpenRouter: one key, hundreds of models (ADR-015) --------------------
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Free, 27B, 262k context - and far stronger in Arabic than anything that
    # fits on this laptop. `scripts/list_models.py` shows the current options.
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    # "low", "medium" or "high" for a reasoning model; empty leaves it to the
    # provider. Low keeps answers inside n8n's timeout: see openrouter_client.
    openrouter_reasoning_effort: str = "low"
    # Tried by OpenRouter, in order, when the main model errors or is
    # overloaded (Phase 13). Comma-separated; empty for none. Super drifts
    # into Latin-script words more than Ultra (ADR-015 benchmark), but an
    # answer with a stray French word beats no answer.
    openrouter_fallback_models: str = "nvidia/nemotron-3-super-120b-a12b:free"

    # --- Ollama: a model running on this machine ------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # --- Anthropic, used from Phase 5 ----------------------------------------
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5"

    # --- Telegram, used from Phase 3 -----------------------------------------
    telegram_bot_token: SecretStr = SecretStr("")
    # Kept as a raw string on purpose: pydantic-settings tries to JSON-decode
    # env vars typed as list/set, which would break on "123,456". We parse it
    # ourselves in `telegram_allowed_ids` below.
    telegram_allowed_user_ids: str = ""

    # --- n8n ------------------------------------------------------------------
    # ADR-011: Cloud during development, back to the container in Phase 15.
    n8n_base_url: str = "http://localhost:5678"

    # --- Ingestion (Phase 6) --------------------------------------------------
    # Where source documents live. Gitignored: these are not ours to commit.
    documents_dir: str = "material"
    # OCR results are cached per page so a run can be resumed. Re-reading 232
    # pages because the laptop slept is not a cost worth paying twice.
    ocr_cache_dir: str = "data/ocr_cache"
    # Vision models that read Arabic and write LaTeX, tried in order until one
    # produces output that passes the quality gate. A chain rather than a
    # single model because the failures measured on this corpus are of two
    # kinds that no one model avoids: free models return 429 unpredictably,
    # and each model has pages it mangles *consistently* - dots-3 emits the
    # Chinese word for "ending" on page 60 of the maths book, six times out of
    # six. Retrying a systematic failure is just waiting; another model is the
    # only thing that helps. Comma-separated. See ADR-016.
    ocr_models: str = (
        "dots-studio/dots-3-note-preview:free,"
        "qwen/qwen3.8-27b:free,"
        "google/gemma-4-31b-it:free"
    )
    # Share of letters that must be Arabic for a page to be accepted. Very
    # low on purpose: page 60 of the maths book measured 14% Arabic by letter,
    # because a page of exercises is mostly LaTeX and rac and \lim are
    # Latin. This is a coarse net for a page that came back translated into
    # English, not a judgement about how much mathematics a page contains.
    # The foreign-script check is the precise one.
    ocr_min_arabic_ratio: float = 0.10
    # Four, not more: the chain is what provides resilience here. Retrying a
    # systematic failure past a few attempts is just waiting, and a 226-page
    # run cannot afford six backoffs per model per page.
    ocr_max_retries: int = 4
    # A hard ceiling on how long one page may take, across every model and
    # every retry. Without it the arithmetic is brutal: 3 models x 4 attempts
    # x a 300s request timeout is 62 minutes, and a run really did sit on one
    # page for 50 of them. A page that cannot be read in five minutes is a
    # page to look at by hand, not one to keep waiting for.
    ocr_page_budget_seconds: int = 300
    # Stop the whole run after this many pages fail in a row. A per-page budget
    # bounds one bad page; it does nothing when every page is failing. An
    # overnight outage once cost 873 minutes - 190 pages, each patiently
    # exhausting three models and four retries against a network that was
    # simply gone. Several consecutive total failures is not bad luck, it is a
    # condition the run cannot fix by continuing.
    ocr_abort_after_failures: int = 5
    # Per request. Pages measured at 8-13 seconds, so this is ample headroom
    # and still far below the point where a hang costs the whole run.
    ocr_request_timeout_seconds: int = 90
    # Long side, in pixels, that page images are scaled to before OCR. The
    # source scans are ~300 dpi; 1500 keeps the glyphs legible while keeping
    # the request small.
    ocr_image_max_px: int = 1500

    # --- Embeddings (Phase 6) -------------------------------------------------
    # Multilingual, because the corpus is Arabic (ADR-003 as amended by
    # ADR-016). Changing this invalidates every stored vector and forces a
    # full re-ingestion, so it is not a casual edit.
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = 8

    # --- Qdrant ---------------------------------------------------------------
    # 127.0.0.1, not localhost: on Windows, localhost tries IPv6 first, and a
    # refused connection took 4.6 s to fail instead of failing at once.
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "knowledge_base"

    # --- Retrieval (Phase 7-8) -------------------------------------------------
    # False falls back to Phase 5 behaviour: the model answers from its own
    # knowledge, ungrounded. Useful only while a corpus is still being built,
    # and a deliberate operator choice rather than a silent fallback - an
    # unavailable knowledge base refuses instead of quietly inventing.
    retrieval_enabled: bool = True
    # Load the embedding model at startup instead of on the first question
    # (Phase 13). Off in tests, which never need the real model.
    retrieval_warm_on_start: bool = True

    # --- Retrieval (Phase 7) --------------------------------------------------
    # How many passages a search returns before the threshold is applied.
    retrieval_top_k: int = 5
    # Cosine similarity below which a passage is treated as not found. The
    # single most consequential number in the system: too low and the
    # assistant answers from near-misses, which is exactly how a grounded
    # system learns to invent. Phase 14 tunes it against an eval set; until
    # then it is a deliberate guess, stated rather than hidden.
    retrieval_score_threshold: float = 0.45
    # Characters of retrieved context passed to the model. A budget, not a
    # limit on the answer: more passages crowd out the question.
    retrieval_context_chars: int = 6000

    # --- Rate limit (Phase 12) --------------------------------------------------
    # Messages per user per window. Far above how fast a person asks
    # questions, far below what drains fifty model requests a day.
    rate_limit_messages: int = 20
    rate_limit_window_s: int = 600

    # --- Agent (Phase 10) ------------------------------------------------------
    # True lets the model decide when to search; false brings back Phase 8's
    # search-on-every-message pipeline. Only providers that can call tools
    # use the agent (ADR-018); the others always get the pipeline.
    agent_enabled: bool = True
    # Rounds of tool calls before the model must answer with what it has.
    # Each round is one model request - one of fifty a day on the free tier.
    agent_max_rounds: int = 3

    # --- Conversation memory (Phase 9) ----------------------------------------
    # False makes every message stand alone again, as before Phase 9.
    memory_enabled: bool = True
    # How many earlier messages (questions and answers both) the model sees.
    # Eight is four exchanges: enough for "and if he was armed?" to know what
    # "he" is, short enough that the passages still dominate the prompt.
    memory_messages: int = 8
    # A long legal answer can run to 2000 characters, and history competes
    # with the passages for the model's attention. Older turns beyond this
    # budget are dropped first.
    memory_max_chars: int = 6000

    # --- Postgres -------------------------------------------------------------
    postgres_user: str = "assistant"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "assistant"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- Derived values -------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn(self) -> str:
        """Connection string assembled from the parts above.

        We store the parts (not a single URL) because docker-compose needs
        them individually - so both Python and Compose read the same .env.
        """
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def ocr_model_list(self) -> list[str]:
        """`ocr_models` split into the chain it describes."""
        return [m.strip() for m in self.ocr_models.split(",") if m.strip()]

    @property
    def telegram_allowed_ids(self) -> set[int]:
        """Parse "123,456" into {123, 456}. Empty string -> empty set.

        An empty set means DENY EVERYONE. Security defaults must be closed:
        a misconfigured allowlist should lock you out, never let strangers in.
        """
        raw = self.telegram_allowed_user_ids.strip()
        if not raw:
            return set()
        return {int(part.strip()) for part in raw.split(",") if part.strip()}

    def describe(self) -> dict[str, str]:
        """A log-safe snapshot of the configuration.

        Secrets are reported only as set/not set - never as values. Anything
        printed by this method is safe to paste into a bug report.
        """

        def secret(value: SecretStr) -> str:
            return "set" if value.get_secret_value() else "NOT SET"

        return {
            "app_env": self.app_env,
            "log_level": self.log_level,
            "api": f"{self.api_host}:{self.api_port}",
            "n8n_base_url": self.n8n_base_url,
            "qdrant_url": self.qdrant_url,
            "qdrant_collection": self.qdrant_collection,
            "embedding_model": self.embedding_model,
            "ocr_models": self.ocr_models,
            "postgres": f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}",
            "llm_provider": self.llm_provider,
            "anthropic_model": self.anthropic_model,
            "ollama_model": self.ollama_model,
            "openrouter_model": self.openrouter_model,
            "openrouter_api_key": secret(self.openrouter_api_key),
            "internal_api_key": secret(self.internal_api_key),
            "anthropic_api_key": secret(self.anthropic_api_key),
            "telegram_bot_token": secret(self.telegram_bot_token),
            "telegram_allowed_ids": str(sorted(self.telegram_allowed_ids)) or "[]",
        }


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton.

    `lru_cache` means the .env file is read and validated exactly once per
    process, no matter how many modules call this. Cheap, and it guarantees
    every part of the app sees identical configuration.
    """
    return Settings()
