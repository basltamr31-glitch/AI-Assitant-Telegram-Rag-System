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
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Internal auth (n8n -> Python), used from Phase 4 --------------------
    internal_api_key: SecretStr = SecretStr("")

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

    # --- Qdrant ---------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_base"

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
            "postgres": f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}",
            "anthropic_model": self.anthropic_model,
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
