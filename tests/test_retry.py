import uuid

import pytest

from app.orm import AccountORM, ExternalCallAttemptORM, RunORM
from infra.circuit_breaker import CircuitBreaker
from infra.retry import CircuitOpenError, NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import CallStatus, RunStatus


def _bare_run(db_session) -> RunORM:
    """A Run row with no discovery attached - these tests are exercising
    call_with_retry in isolation, not the discovery stage, so using
    start_run() here (which now also calls call_with_retry itself, for the
    discovery page fetch) would add an extra attempt row these tests
    aren't expecting and don't care about."""

    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _sleeps_recorded():
    """A fake sleep function that records delays instead of actually
    waiting, so these tests run in milliseconds instead of seconds."""

    delays = []

    def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    return fake_sleep, delays


def test_succeeds_on_first_attempt_records_one_success_row(db_session):
    run = _bare_run(db_session)

    result = call_with_retry(
        lambda: "ok",
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="discovery",
    )

    assert result == "ok"
    attempts = (
        db_session.query(ExternalCallAttemptORM).filter_by(run_id=run.id).all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == CallStatus.SUCCESS
    assert attempts[0].attempt_number == 1


def test_retries_on_retryable_error_then_succeeds(db_session):
    run = _bare_run(db_session)
    fake_sleep, delays = _sleeps_recorded()

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("timeout after 10s")
        return "recovered"

    result = call_with_retry(
        flaky,
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        max_attempts=3,
        base_delay_seconds=1.0,
        sleep=fake_sleep,
        jitter=lambda: 1.0,  # deterministic - Step 19 added jitter by default
    )

    assert result == "recovered"
    assert calls["n"] == 3
    # exponential backoff: 1s, then 2s between the two failed attempts
    assert delays == [1.0, 2.0]

    attempts = (
        db_session.query(ExternalCallAttemptORM)
        .filter_by(run_id=run.id)
        .order_by(ExternalCallAttemptORM.attempt_number)
        .all()
    )
    assert [a.status for a in attempts] == [
        CallStatus.FAILED_RETRYABLE,
        CallStatus.FAILED_RETRYABLE,
        CallStatus.SUCCESS,
    ]


def test_exhausts_retries_and_raises_last_error(db_session):
    run = _bare_run(db_session)
    fake_sleep, _ = _sleeps_recorded()

    def always_times_out():
        raise RetryableError("timeout after 10s")

    with pytest.raises(RetryableError):
        call_with_retry(
            always_times_out,
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            max_attempts=3,
            base_delay_seconds=0.01,
            sleep=fake_sleep,
        )

    attempts = (
        db_session.query(ExternalCallAttemptORM)
        .filter_by(run_id=run.id)
        .order_by(ExternalCallAttemptORM.attempt_number)
        .all()
    )
    assert len(attempts) == 3
    assert attempts[-1].status == CallStatus.FAILED_EXHAUSTED


def test_non_retryable_error_fails_immediately_without_retrying(db_session):
    run = _bare_run(db_session)
    fake_sleep, delays = _sleeps_recorded()

    calls = {"n": 0}

    def bad_auth():
        calls["n"] += 1
        raise NonRetryableError("401 unauthorized")

    with pytest.raises(NonRetryableError):
        call_with_retry(
            bad_auth,
            session=db_session,
            run_id=run.id,
            provider="hubspot",
            operation="crm_upsert",
            max_attempts=5,
            sleep=fake_sleep,
        )

    assert calls["n"] == 1  # never retried
    assert delays == []  # never slept

    attempts = (
        db_session.query(ExternalCallAttemptORM).filter_by(run_id=run.id).all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == CallStatus.FAILED_NON_RETRYABLE


def test_attempts_are_scoped_per_item_via_account_id(db_session):
    """Two different accounts failing in the same run produce separate,
    independently-attributable attempt logs - this is what lets one
    account's failure be isolated from another's, per the technical audit's
    per-item failure isolation model.

    Uses two real, persisted AccountORM rows rather than bare UUIDs -
    account_id is a real foreign key (enforced by Postgres; SQLite doesn't
    enforce it by default, which is exactly how an earlier version of this
    test passed against SQLite but failed against real Postgres)."""

    run = _bare_run(db_session)
    account_a = AccountORM(domain="account-a.example.com", name="Account A")
    account_b = AccountORM(domain="account-b.example.com", name="Account B")
    db_session.add_all([account_a, account_b])
    db_session.flush()
    account_a, account_b = account_a.id, account_b.id

    for account_id in (account_a, account_b):
        with pytest.raises(NonRetryableError):
            call_with_retry(
                lambda: (_ for _ in ()).throw(NonRetryableError("bad request")),
                session=db_session,
                run_id=run.id,
                account_id=account_id,
                provider="apollo",
                operation="account_enrichment",
            )

    attempts = (
        db_session.query(ExternalCallAttemptORM).filter_by(run_id=run.id).all()
    )
    assert {a.account_id for a in attempts} == {account_a, account_b}


# --- Step 19: jittered backoff -----------------------------------------

def test_jitter_scales_the_backoff_within_bounds(db_session):
    run = _bare_run(db_session)
    fake_sleep, delays = _sleeps_recorded()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RetryableError("timeout")
        return "ok"

    call_with_retry(
        flaky,
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        base_delay_seconds=1.0,
        sleep=fake_sleep,
        jitter=lambda: 0.5,  # deterministic, but a real jitter value (not 1.0)
    )

    assert delays == [0.5]  # base_delay(1.0) * 2**(1-1) * jitter(0.5)


def test_default_jitter_produces_a_delay_within_the_documented_range(db_session):
    """No injected jitter this time - proves the real default is bounded,
    not just that an injected fake works."""

    run = _bare_run(db_session)
    fake_sleep, delays = _sleeps_recorded()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RetryableError("timeout")
        return "ok"

    call_with_retry(
        flaky,
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        base_delay_seconds=1.0,
        sleep=fake_sleep,
    )

    assert 0.5 <= delays[0] <= 1.5  # base_delay(1.0) * 2**0 * uniform(0.5, 1.5)


# --- Step 19: rate-limit-aware pacing (Retry-After) ---------------------

def test_retry_after_on_the_error_overrides_computed_backoff(db_session):
    run = _bare_run(db_session)
    fake_sleep, delays = _sleeps_recorded()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RetryableError("rate limited", retry_after_seconds=7.0)
        return "ok"

    call_with_retry(
        flaky,
        session=db_session,
        run_id=run.id,
        provider="hubspot",
        operation="crm_sync",
        base_delay_seconds=1.0,
        sleep=fake_sleep,
        jitter=lambda: 1.0,
    )

    assert delays == [7.0]  # honored verbatim, not the computed 1.0*2**0*jitter


# --- Step 19: circuit breaker integration -------------------------------

def test_circuit_open_short_circuits_without_calling_fn(db_session):
    run = _bare_run(db_session)
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure("apollo")  # opens the circuit
    calls = {"n": 0}

    def never_called():
        calls["n"] += 1
        return "should not happen"

    with pytest.raises(CircuitOpenError):
        call_with_retry(
            never_called,
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            circuit_breaker=cb,
        )

    assert calls["n"] == 0

    attempt = (
        db_session.query(ExternalCallAttemptORM)
        .filter_by(run_id=run.id, provider="apollo")
        .one()
    )
    assert attempt.status == CallStatus.CIRCUIT_OPEN
    assert attempt.attempt_number == 0


def test_exhausted_retries_trip_the_circuit_for_subsequent_calls(db_session):
    run = _bare_run(db_session)
    cb = CircuitBreaker(failure_threshold=1)
    fake_sleep, _ = _sleeps_recorded()

    with pytest.raises(RetryableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(RetryableError("down")),
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            max_attempts=1,
            circuit_breaker=cb,
            sleep=fake_sleep,
        )

    # a second, independent account hitting the same provider must not
    # even attempt a call now - this is the actual point of a circuit
    # breaker: stop burning every remaining item's retry budget on a
    # provider that's clearly down.
    calls = {"n": 0}
    with pytest.raises(CircuitOpenError):
        call_with_retry(
            lambda: calls.update(n=calls["n"] + 1),
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            circuit_breaker=cb,
        )
    assert calls["n"] == 0


def test_a_non_retryable_error_also_trips_the_circuit(db_session):
    """A broken API key fails every call identically - exactly the case a
    circuit breaker should stop burning attempts on fastest."""

    run = _bare_run(db_session)
    cb = CircuitBreaker(failure_threshold=1)

    with pytest.raises(NonRetryableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(NonRetryableError("bad api key")),
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            circuit_breaker=cb,
        )

    with pytest.raises(CircuitOpenError):
        call_with_retry(
            lambda: "unreachable",
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            circuit_breaker=cb,
        )


def test_a_success_resets_the_circuit_so_a_later_failure_does_not_immediately_trip_it(db_session):
    run = _bare_run(db_session)
    cb = CircuitBreaker(failure_threshold=2)

    call_with_retry(
        lambda: "ok",
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        circuit_breaker=cb,
    )

    with pytest.raises(NonRetryableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(NonRetryableError("one bad call")),
            session=db_session,
            run_id=run.id,
            provider="apollo",
            operation="account_enrichment",
            circuit_breaker=cb,
        )

    # threshold is 2 consecutive failures - only one has happened since
    # the reset, so the circuit must still be closed
    result = call_with_retry(
        lambda: "still working",
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        circuit_breaker=cb,
    )
    assert result == "still working"
