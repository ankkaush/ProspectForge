"""Application configuration, loaded exclusively from environment variables.

Why env vars and not a config file with real values in it: secrets (API
key, database credentials) must never be committed to the repository. This
module fails fast at import time if a required variable is missing -
matching the roadmap's stated failure scenario for this step: a missing env
var should produce a clear startup error, not a runtime KeyError buried
inside a request handler.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Required - no default. If this isn't set, the app refuses to start
    # rather than silently running with an empty/guessable key.
    prospectforge_api_key: str

    # Has a sensible local-dev default, but is expected to be overridden via
    # env var in any real deployment.
    database_url: str = "postgresql+psycopg://prospectforge:prospectforge@localhost:5432/prospectforge"

    log_level: str = "INFO"
    environment: str = "development"

    # Optional at the app level - the app boots fine without it (health
    # checks, etc. don't need it). It's only required when a run actually
    # tries to use the real Apollo provider, at which point the discovery
    # service raises a clear error rather than the app failing to start.
    apollo_api_key: str = ""

    # How many results (across however many pages that takes, at 100/page)
    # a single discovery run pulls by default. Kept conservative given
    # Apollo's free tier - callers (CLI/API, later) can override per run.
    discovery_max_results: int = 100

    # Which DiscoveryProvider get_default_discovery_provider() constructs:
    # "csv" or "apollo". Defaults to "csv" because a live check (2026-08-19)
    # found Apollo's free plan has no search API access at all - see
    # ADR-003's addendum. Switch to "apollo" once real API access exists;
    # nothing else in the codebase needs to change (see ADR-005).
    discovery_provider: str = "csv"

    # Optional at the app level, same reasoning as apollo_api_key - only
    # required when a run actually tries to use the real Claude
    # web-search research provider (Step 11).
    anthropic_api_key: str = ""

    # "csv" (default) or "apollo". Same reasoning as discovery_provider -
    # Apollo's people-search endpoint is also gated on the free plan (see
    # ADR-003's addendum, re-confirmed at Step 12).
    people_discovery_provider: str = "csv"

    # Which persona config (prospectforge/persona/configs/*.yaml) defines
    # who counts as a relevant decision-maker.
    people_discovery_persona_id: str = "primary-buyer-v1"

    # "csv" (default) or "apollo". Same reasoning as discovery_provider -
    # Apollo's people/match endpoint is also gated on the free plan (see
    # ADR-003's addendum, re-confirmed at Step 13).
    contact_enrichment_provider: str = "csv"

    # "deterministic" (default) or "anthropic". Only controls how
    # QualificationResult.rationale_text is *phrased* - the qualification
    # verdict/confidence/reasons are always decided by the deterministic
    # engine (prospectforge/qualification/engine.py) regardless of this
    # setting. Defaults to "deterministic" so the standard pipeline never
    # calls Anthropic for qualification; "anthropic" is available as an
    # optional, swappable presentation-layer enhancement (see
    # qualification/service.py).
    qualification_rationale_provider: str = "deterministic"

    # Optional at the app level, same reasoning as apollo_api_key /
    # anthropic_api_key - only required when a CRM sync actually tries to
    # reach the real HubSpotAdapter (Step 18). A HubSpot private app
    # token, not an OAuth client secret - simplest auth method for a
    # single-account developer/sandbox integration (see ADR-004).
    hubspot_api_key: str = ""

    # Optional at the app level, same reasoning as every other API key -
    # the app runs fine without it, Sentry error tracking is simply
    # disabled (infra/observability.py). Get one from https://sentry.io
    # (free tier) -> Settings -> Projects -> Client Keys (DSN).
    sentry_dsn: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings loader. A function (not a bare module-level constant)
    so tests can point DATABASE_URL etc. at a throwaway database before the
    app reads settings for the first time."""

    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test-only escape hatch: forces the next get_settings() call to
    re-read the environment. Real app code never needs this."""

    global _settings
    _settings = None
