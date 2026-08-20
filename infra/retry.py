"""Shared retry/backoff utility - the minimal version the technical audit
moved from Step 19 into the foundation, because every adapter built from
Step 7 onward (Apollo, the LLM, HubSpot) needs it from the moment it's
written, not retrofitted later.

Two things this utility gets right on purpose, kept deliberately simple:

1. Retryable vs. non-retryable errors are two different exception types,
   not a status-code lookup buried in each adapter. An adapter raises
   `RetryableError` for a timeout/rate-limit/5xx, `NonRetryableError` for a
   bad request/bad auth - `call_with_retry` doesn't need to know anything
   about Apollo or HubSpot specifically to make the right call.
2. Every attempt - success or failure - is persisted as an
   ExternalCallAttempt row before `call_with_retry` returns or raises. This
   is what makes a failure debuggable after the fact instead of only
   existing as a line in a log stream that scrolled away.

Step 19 (reliability hardening) adds three things on top of this
foundation, all in this one file plus infra/circuit_breaker.py:

3. Circuit-breaking: a provider that's failing repeatedly gets skipped
   entirely (no network attempt) for a cooldown window, via
   infra.circuit_breaker - see that module's docstring for why a
   process-local registry is the right scope here.
4. Jittered backoff: the exponential delay between attempts gets a
   random multiplier, so many accounts hitting the same down provider at
   once don't all retry in lockstep.
5. Rate-limit-aware pacing: RetryableError can carry the provider's own
   Retry-After hint (retry_after_seconds) - when present, it's honored
   directly instead of the computed exponential delay.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Callable, Optional, TypeVar

from sqlalchemy.orm import Session

from app.orm import ExternalCallAttemptORM
from infra.circuit_breaker import CircuitBreaker, default_circuit_breaker
from prospectforge.models.enums import CallStatus

T = TypeVar("T")


class RetryableError(Exception):
    """Raise this from an adapter for a failure that's worth retrying:
    a timeout, a 429 rate-limit, a 5xx server error.

    retry_after_seconds: set this when the provider tells you exactly how
    long to wait (a 429's Retry-After header, for example) - honored
    directly by call_with_retry instead of the computed exponential
    delay, since the provider's own number is more accurate than a guess.
    """

    def __init__(self, message: str, *, retry_after_seconds: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class CircuitOpenError(RetryableError):
    """Raised when a provider's circuit is currently open - no network
    attempt was made for this call at all. Still a RetryableError (a
    later call, once the circuit's cooldown elapses, may succeed), so
    every existing `except (RetryableError, NonRetryableError)` call site
    across the pipeline already handles this correctly without change."""


class NonRetryableError(Exception):
    """Raise this from an adapter for a failure that will never succeed on
    retry: a 400 bad request, a 401/403 auth failure. Retrying these just
    burns time and attempts for no benefit."""


def call_with_retry(
    fn: Callable[[], T],
    *,
    session: Session,
    provider: str,
    operation: str,
    run_id: Optional[uuid.UUID] = None,
    account_id: Optional[uuid.UUID] = None,
    contact_id: Optional[uuid.UUID] = None,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    circuit_breaker: CircuitBreaker = default_circuit_breaker,
    jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.5),
) -> T:
    """Call `fn()`, retrying on RetryableError with jittered exponential
    backoff (base_delay * 2**(attempt-1) * jitter(), or the error's own
    retry_after_seconds when it provides one), up to max_attempts. Every
    attempt is recorded as an ExternalCallAttempt row. Raises immediately
    on NonRetryableError, without consuming further attempts. Raises the
    last RetryableError once attempts are exhausted.

    Before attempting anything, checks circuit_breaker for this provider -
    if it's open, raises CircuitOpenError immediately without a network
    call (still logged, as CallStatus.CIRCUIT_OPEN). A whole invocation's
    outcome (not each internal attempt) is what the circuit tracks: one
    success resets it, one exhausted/non-retryable failure counts once.

    run_id is optional - every pipeline-stage caller (steps 7-16) has a
    real Run to attribute the call to, but CRM sync (step 18) doesn't: it
    processes whatever's sitting at review_decision=APPROVED across all
    runs, on the reviewer's own schedule, not as part of any one run.
    """

    if not circuit_breaker.allow_call(provider):
        _record_attempt(
            session,
            run_id=run_id,
            account_id=account_id,
            contact_id=contact_id,
            provider=provider,
            operation=operation,
            attempt_number=0,
            status=CallStatus.CIRCUIT_OPEN,
            error_message=f"circuit open for provider={provider} - no call attempted",
        )
        raise CircuitOpenError(f"circuit open for provider={provider} - no call attempted")

    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
        except NonRetryableError as exc:
            _record_attempt(
                session,
                run_id=run_id,
                account_id=account_id,
                contact_id=contact_id,
                provider=provider,
                operation=operation,
                attempt_number=attempt,
                status=CallStatus.FAILED_NON_RETRYABLE,
                error_message=str(exc),
            )
            circuit_breaker.record_failure(provider)
            raise
        except RetryableError as exc:
            last_error = exc
            is_final_attempt = attempt == max_attempts
            _record_attempt(
                session,
                run_id=run_id,
                account_id=account_id,
                contact_id=contact_id,
                provider=provider,
                operation=operation,
                attempt_number=attempt,
                status=(
                    CallStatus.FAILED_EXHAUSTED
                    if is_final_attempt
                    else CallStatus.FAILED_RETRYABLE
                ),
                error_message=str(exc),
            )
            if is_final_attempt:
                circuit_breaker.record_failure(provider)
                raise
            delay = (
                exc.retry_after_seconds
                if exc.retry_after_seconds is not None
                else base_delay_seconds * (2 ** (attempt - 1)) * jitter()
            )
            sleep(delay)
            continue
        else:
            _record_attempt(
                session,
                run_id=run_id,
                account_id=account_id,
                contact_id=contact_id,
                provider=provider,
                operation=operation,
                attempt_number=attempt,
                status=CallStatus.SUCCESS,
            )
            circuit_breaker.record_success(provider)
            return result

    # Unreachable in practice (the loop always returns or raises), but keeps
    # type checkers happy and fails loudly instead of returning None if it
    # ever were.
    raise last_error or RuntimeError("call_with_retry exited without a result")


def _record_attempt(
    session: Session,
    *,
    run_id: Optional[uuid.UUID],
    account_id: Optional[uuid.UUID],
    contact_id: Optional[uuid.UUID],
    provider: str,
    operation: str,
    attempt_number: int,
    status: CallStatus,
    error_message: Optional[str] = None,
) -> None:
    session.add(
        ExternalCallAttemptORM(
            run_id=run_id,
            account_id=account_id,
            contact_id=contact_id,
            provider=provider,
            operation=operation,
            attempt_number=attempt_number,
            status=status,
            error_message=error_message,
        )
    )
    session.flush()
