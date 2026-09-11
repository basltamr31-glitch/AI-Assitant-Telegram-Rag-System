"""Tests for configuration parsing.

These do not touch the network or the containers - they test our own logic
only. That is what makes them *unit* tests, and why they run in milliseconds.
"""

from app.config import Settings


def test_allowlist_parses_comma_separated_ids() -> None:
    settings = Settings(telegram_allowed_user_ids="123, 456,789")
    assert settings.telegram_allowed_ids == {123, 456, 789}


def test_empty_allowlist_denies_everyone() -> None:
    """Security default: an unset allowlist must lock everyone out."""
    settings = Settings(telegram_allowed_user_ids="")
    assert settings.telegram_allowed_ids == set()


def test_postgres_dsn_is_assembled_from_parts() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        postgres_host="h",
        postgres_port=1234,
    )
    assert settings.postgres_dsn == "postgresql://u:p@h:1234/d"


def test_secrets_are_not_exposed_by_describe() -> None:
    """describe() must never leak a secret value into logs."""
    settings = Settings(anthropic_api_key="sk-ant-super-secret")
    described = settings.describe()
    assert described["anthropic_api_key"] == "set"
    assert "super-secret" not in str(described)
