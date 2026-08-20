"""Step 22: Sentry error tracking + provider failure-rate alerting.

No real Sentry account exists as of this step (no SENTRY_DSN in .env) -
these tests use Sentry's own recommended pattern for testing without
network access: a custom Transport that captures envelopes in memory
instead of sending them, passed directly to sentry_sdk.init(). This
proves the actual integration wiring (LoggingIntegration, tag
correlation) works, not just that the functions don't crash.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from app.logging import log_context
from app.orm import ExternalCallAttemptORM, RunORM
from infra import observability
from prospectforge.models.enums import CallStatus, RunStatus


class _CapturingTransport(Transport):
    """Records every envelope instead of sending it anywhere."""

    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)

    @property
    def events(self):
        return [e.get_event() for e in self.envelopes if e.get_event() is not None]


def _init_sentry_with_capturing_transport() -> _CapturingTransport:
    transport = _CapturingTransport()
    sentry_sdk.init(
        dsn="https://public@example.ingest.sentry.io/1",
        transport=transport,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.WARNING)],
    )
    return transport


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


# --- init_observability(): optional, same pattern as every other key ---

def test_init_observability_is_a_no_op_without_a_dsn(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "test-api-key")
    monkeypatch.setenv("SENTRY_DSN", "")
    reset_settings_cache()
    observability.reset_for_testing()

    observability.init_observability()

    assert observability.is_active() is False


def test_init_observability_is_idempotent(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("PROSPECTFORGE_API_KEY", "test-api-key")
    monkeypatch.setenv("SENTRY_DSN", "")
    reset_settings_cache()
    observability.reset_for_testing()

    observability.init_observability()
    observability.init_observability()  # must not raise or re-configure

    assert observability.is_active() is False


# --- correlation tags -----------------------------------------------------

def test_set_correlation_tags_is_a_no_op_when_sentry_is_inactive():
    sentry_sdk.get_global_scope().set_client(None)  # NonRecordingClient - genuinely inactive
    observability.set_correlation_tags(run_id="abc")  # must not raise


def test_log_context_pushes_and_restores_sentry_tags():
    _init_sentry_with_capturing_transport()

    run_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    with log_context(run_id=run_id):
        assert sentry_sdk.get_current_scope()._tags.get("run_id") == run_id
        with log_context(account_id=account_id):
            assert sentry_sdk.get_current_scope()._tags.get("run_id") == run_id
            assert sentry_sdk.get_current_scope()._tags.get("account_id") == account_id
        # account_id tag cleared on exiting the nested context, run_id remains
        assert "account_id" not in sentry_sdk.get_current_scope()._tags
        assert sentry_sdk.get_current_scope()._tags.get("run_id") == run_id


# --- the exit criteria: a forced error surfaces in Sentry with context -

def test_a_forced_run_level_error_surfaces_in_sentry_with_run_id_context(db_session):
    """Simulates app/trigger.py's own failure path: logger.error() inside
    a log_context(run_id=...) block. Proves the actual wiring - the
    LoggingIntegration capturing it, and the tag correlation - not just
    that the functions are individually callable."""

    transport = _init_sentry_with_capturing_transport()
    trigger_logger = logging.getLogger("prospectforge.trigger")
    run_id = str(uuid.uuid4())

    with log_context(run_id=run_id):
        trigger_logger.error("run failed during discovery: simulated provider outage")

    assert len(transport.events) == 1
    event = transport.events[0]
    assert event["level"] == "error"
    assert "simulated provider outage" in event["logentry"]["message"]
    assert event["tags"]["run_id"] == run_id


# --- provider failure-rate alerting --------------------------------------

def _attempt(db_session, run, *, provider="apollo", status, minutes_ago=0):
    db_session.add(
        ExternalCallAttemptORM(
            run_id=run.id,
            provider=provider,
            operation="account_enrichment",
            attempt_number=1,
            status=status,
            requested_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        )
    )
    db_session.flush()


def test_compute_provider_failure_rate_with_no_attempts(db_session):
    stats = observability.compute_provider_failure_rate(db_session, "apollo")
    assert stats["total_attempts"] == 0
    assert stats["failure_rate"] is None


def test_compute_provider_failure_rate_counts_correctly(db_session):
    run = _bare_run(db_session)
    for _ in range(3):
        _attempt(db_session, run, status=CallStatus.SUCCESS)
    for _ in range(2):
        _attempt(db_session, run, status=CallStatus.FAILED_EXHAUSTED)

    stats = observability.compute_provider_failure_rate(db_session, "apollo")

    assert stats["total_attempts"] == 5
    assert stats["failed_attempts"] == 2
    assert stats["failure_rate"] == 0.4


def test_compute_provider_failure_rate_only_counts_the_given_provider(db_session):
    run = _bare_run(db_session)
    _attempt(db_session, run, provider="apollo", status=CallStatus.FAILED_EXHAUSTED)
    _attempt(db_session, run, provider="anthropic", status=CallStatus.FAILED_EXHAUSTED)

    stats = observability.compute_provider_failure_rate(db_session, "apollo")

    assert stats["total_attempts"] == 1


def test_compute_provider_failure_rate_respects_the_time_window(db_session):
    run = _bare_run(db_session)
    _attempt(db_session, run, status=CallStatus.FAILED_EXHAUSTED, minutes_ago=60)  # outside window

    stats = observability.compute_provider_failure_rate(db_session, "apollo", window_minutes=15)

    assert stats["total_attempts"] == 0


def test_check_and_alert_does_not_fire_below_the_minimum_attempt_floor(db_session):
    run = _bare_run(db_session)
    _attempt(db_session, run, status=CallStatus.FAILED_EXHAUSTED)  # 1 failure, 1 attempt = 100% but too few

    alert = observability.check_and_alert_on_failure_rate(db_session, "apollo", min_attempts=5)

    assert alert is None


def test_check_and_alert_does_not_fire_below_threshold(db_session):
    run = _bare_run(db_session)
    for _ in range(9):
        _attempt(db_session, run, status=CallStatus.SUCCESS)
    _attempt(db_session, run, status=CallStatus.FAILED_EXHAUSTED)  # 10%

    alert = observability.check_and_alert_on_failure_rate(db_session, "apollo", threshold=0.5, min_attempts=5)

    assert alert is None


def test_check_and_alert_fires_above_threshold_and_logs_a_warning(db_session, caplog):
    run = _bare_run(db_session)
    for _ in range(6):
        _attempt(db_session, run, status=CallStatus.FAILED_EXHAUSTED)
    for _ in range(4):
        _attempt(db_session, run, status=CallStatus.SUCCESS)

    with caplog.at_level(logging.WARNING, logger="prospectforge.observability"):
        alert = observability.check_and_alert_on_failure_rate(
            db_session, "apollo", threshold=0.5, min_attempts=5
        )

    assert alert is not None
    assert alert["failure_rate"] == 0.6
    assert any("failure rate alert" in record.message for record in caplog.records)
