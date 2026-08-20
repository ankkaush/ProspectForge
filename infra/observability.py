"""Sentry error tracking + provider failure-rate alerting (Step 22) -
the polish layer on top of the structured, correlated logging that's
existed since Step 5, not the first introduction of observability.

Sentry is optional at the app level, same pattern as every other API key
in this project (Apollo/Anthropic/HubSpot): SENTRY_DSN unset means
init_observability() is a no-op and the app runs exactly as it did before
this step. Wired via sentry_sdk's LoggingIntegration rather than scattering
explicit sentry_sdk.capture_exception() calls through business logic -
this project already logs every failure via the standard logging module
(app/trigger.py's two logger.error() call sites for run-level failures,
plus this module's own logger.warning() for a failure-rate alert), so
Sentry is configured to treat WARNING-and-above log records as events,
picking up both automatically.

Correlation IDs: app/logging.py's log_context() already binds
run_id/account_id/contact_id as contextvars for JSON logs - this module
adds set_correlation_tags(), which log_context() also calls, so a Sentry
event and its corresponding log line always agree on which run/account/
contact it came from (the "correlation IDs across an async multi-step
pipeline" concept this step names).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.orm import ExternalCallAttemptORM
from prospectforge.models.enums import CallStatus

logger = logging.getLogger("prospectforge.observability")

_initialized = False

DEFAULT_FAILURE_RATE_WINDOW_MINUTES = 15
DEFAULT_FAILURE_RATE_THRESHOLD = 0.5
DEFAULT_FAILURE_RATE_MIN_ATTEMPTS = 5  # don't alert on 1-of-1 - too noisy to be useful

_FAILURE_STATUSES = {
    CallStatus.FAILED_NON_RETRYABLE,
    CallStatus.FAILED_EXHAUSTED,
    CallStatus.CIRCUIT_OPEN,
}


def init_observability() -> None:
    """Call once at process startup (app/main.py, prospectforge/cli.py) -
    idempotent, so importing a module that also calls it (tests, a second
    CLI command in the same process) is harmless."""

    global _initialized
    if _initialized:
        return
    _initialized = True

    from app.config import get_settings

    settings = get_settings()
    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN not set - Sentry error tracking disabled")
        return

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.0,  # error tracking only - no perf tracing needed for this project
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.WARNING)],
    )
    logger.info("Sentry error tracking initialized environment=%s", settings.environment)


def reset_for_testing() -> None:
    """Test-only escape hatch, same pattern as app/config.py's
    reset_settings_cache() - forces the next init_observability() call to
    actually run instead of no-opping."""

    global _initialized
    _initialized = False


def is_active() -> bool:
    import sentry_sdk

    return sentry_sdk.get_client().is_active()


def set_correlation_tags(
    *,
    run_id: Optional[str] = None,
    account_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> None:
    if not is_active():
        return

    import sentry_sdk

    scope = sentry_sdk.get_current_scope()
    for key, value in (("run_id", run_id), ("account_id", account_id), ("contact_id", contact_id)):
        if value:
            scope.set_tag(key, value)
        else:
            scope.remove_tag(key)


def compute_provider_failure_rate(
    session: Session,
    provider: str,
    *,
    window_minutes: int = DEFAULT_FAILURE_RATE_WINDOW_MINUTES,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pure computation, no alerting decision - separated so the
    threshold/noise-floor policy in check_and_alert_on_failure_rate() is
    testable independently of "what actually counts as a failure.\""""

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=window_minutes)

    attempts = (
        session.query(ExternalCallAttemptORM)
        .filter(
            ExternalCallAttemptORM.provider == provider,
            ExternalCallAttemptORM.requested_at >= since,
        )
        .all()
    )

    total = len(attempts)
    failed = sum(1 for a in attempts if a.status in _FAILURE_STATUSES)

    return {
        "provider": provider,
        "window_minutes": window_minutes,
        "total_attempts": total,
        "failed_attempts": failed,
        "failure_rate": (failed / total) if total else None,
    }


def check_and_alert_on_failure_rate(
    session: Session,
    provider: str,
    *,
    window_minutes: int = DEFAULT_FAILURE_RATE_WINDOW_MINUTES,
    threshold: float = DEFAULT_FAILURE_RATE_THRESHOLD,
    min_attempts: int = DEFAULT_FAILURE_RATE_MIN_ATTEMPTS,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Logs a WARNING (captured by Sentry when configured, per
    init_observability()'s LoggingIntegration) and returns the stats dict
    if the provider's recent failure rate crosses threshold; returns None
    (no alert) otherwise - including when there simply isn't enough
    volume yet to mean anything (min_attempts guards against alerting on
    a single unlucky call)."""

    stats = compute_provider_failure_rate(session, provider, window_minutes=window_minutes, now=now)

    if stats["total_attempts"] < min_attempts or stats["failure_rate"] is None:
        return None
    if stats["failure_rate"] < threshold:
        return None

    logger.warning(
        "provider failure rate alert: provider=%s failure_rate=%.0f%% "
        "(%d/%d attempts failed in the last %d minutes)",
        provider,
        stats["failure_rate"] * 100,
        stats["failed_attempts"],
        stats["total_attempts"],
        window_minutes,
    )
    return stats
