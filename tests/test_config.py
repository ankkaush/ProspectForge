"""Step 5's named failure scenario, which had zero direct test coverage
until Step 21's audit found the gap: a missing required env var should
fail fast with a clear error, not a runtime KeyError buried inside a
request handler. Verified here by actually constructing Settings without
PROSPECTFORGE_API_KEY, isolated from the real environment/`.env` file so
this test's result doesn't depend on what happens to be set locally.
"""

import pydantic
import pytest

from app.config import Settings


def test_missing_required_api_key_fails_fast_with_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("PROSPECTFORGE_API_KEY", raising=False)
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        Settings(_env_file=str(empty_env_file))

    assert "prospectforge_api_key" in str(exc_info.value).lower()


def test_a_present_api_key_constructs_settings_successfully(monkeypatch, tmp_path):
    monkeypatch.delenv("PROSPECTFORGE_API_KEY", raising=False)
    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "a-real-looking-key")
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    settings = Settings(_env_file=str(empty_env_file))

    assert settings.prospectforge_api_key == "a-real-looking-key"


def test_bare_postgres_scheme_is_normalized_to_the_psycopg_driver(monkeypatch, tmp_path):
    """Step 23's real deploy failure: Render's managed Postgres hands out
    a plain postgres:// URL, which SQLAlchemy defaults to the psycopg2
    dialect for - not installed in this project (only psycopg v3 /
    psycopg-binary are). Confirmed live: ModuleNotFoundError: No module
    named 'psycopg2', inside Alembic's env.py during the very first
    deploy."""

    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "a-real-looking-key")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@render-host:5432/dbname")
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    settings = Settings(_env_file=str(empty_env_file))

    assert settings.database_url == "postgresql+psycopg://user:pass@render-host:5432/dbname"


def test_bare_postgresql_scheme_is_also_normalized(monkeypatch, tmp_path):
    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "a-real-looking-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@render-host:5432/dbname")
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    settings = Settings(_env_file=str(empty_env_file))

    assert settings.database_url == "postgresql+psycopg://user:pass@render-host:5432/dbname"


def test_already_correct_driver_scheme_is_left_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "a-real-looking-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@host:5432/dbname")
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    settings = Settings(_env_file=str(empty_env_file))

    assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/dbname"


def test_optional_provider_keys_default_to_empty_not_required(monkeypatch, tmp_path):
    """apollo_api_key/anthropic_api_key/hubspot_api_key are optional at
    the app level - the app boots fine without them; only a run that
    actually tries the real provider raises (see each service's
    get_default_*_provider(), tested separately per stage)."""

    monkeypatch.delenv("PROSPECTFORGE_API_KEY", raising=False)
    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "a-real-looking-key")
    for var in ("APOLLO_API_KEY", "ANTHROPIC_API_KEY", "HUBSPOT_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    empty_env_file = tmp_path / ".env"
    empty_env_file.write_text("")

    settings = Settings(_env_file=str(empty_env_file))

    assert settings.apollo_api_key == ""
    assert settings.anthropic_api_key == ""
    assert settings.hubspot_api_key == ""
