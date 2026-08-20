"""A minimal per-provider circuit breaker (Step 19's reliability
hardening): after a provider fails repeatedly, stop attempting real calls
to it for a cooldown window instead of burning every account/contact's
retry budget hammering something that's clearly down.

In-memory, per-process, global-by-provider-name state - deliberately not
persisted or shared across processes. This is a single-process app (no
worker fleet yet), so a module-level registry is the correct scope for
this: sufficient to stop a bad run from hammering a dead provider, and
simple enough not to need its own storage/locking design. A distributed
version (shared state across workers) would be a real redesign, not a
tweak - out of scope until this project actually runs as more than one
process.

Not wired to persist its own state anywhere - CallStatus.CIRCUIT_OPEN
attempts are still logged via the normal ExternalCallAttempt audit trail
in infra/retry.py, so the "is the circuit currently open" fact is
reconstructable from that table even though this module's own state is
volatile.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

FAILURE_THRESHOLD = 5
COOLDOWN_SECONDS = 60.0


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: Optional[float] = None
    trial_in_flight: bool = False


class CircuitBreaker:
    """One instance is shared process-wide via the module-level default
    below; tests construct their own instance to avoid cross-test state
    bleed (see conftest.py's autouse reset for the shared default)."""

    def __init__(
        self,
        *,
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_seconds: float = COOLDOWN_SECONDS,
        clock: "callable" = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._states: Dict[str, _CircuitState] = {}

    def _state_for(self, provider: str) -> _CircuitState:
        return self._states.setdefault(provider, _CircuitState())

    def allow_call(self, provider: str) -> bool:
        """False means: don't even attempt the call - the circuit is
        open. True covers both the normal closed state and the one
        half-open trial call allowed after cooldown; a caller that gets
        True must report the outcome via record_success/record_failure so
        the trial can resolve the circuit one way or the other."""

        with self._lock:
            state = self._state_for(provider)
            if state.opened_at is None:
                return True

            if state.trial_in_flight:
                # Another attempt is already using this window's one
                # trial call - don't let a second caller sneak through
                # and hammer a still-down provider concurrently.
                return False

            elapsed = self._clock() - state.opened_at
            if elapsed < self._cooldown_seconds:
                return False

            state.trial_in_flight = True
            return True

    def record_success(self, provider: str) -> None:
        with self._lock:
            self._states[provider] = _CircuitState()  # fully reset - closed, zero failures

    def record_failure(self, provider: str) -> None:
        with self._lock:
            state = self._state_for(provider)
            state.trial_in_flight = False
            state.consecutive_failures += 1
            if state.opened_at is not None or state.consecutive_failures >= self._failure_threshold:
                state.opened_at = self._clock()

    def reset(self, provider: Optional[str] = None) -> None:
        """Test-only escape hatch (also useful for an operator manually
        clearing a stuck circuit). None clears every provider."""

        with self._lock:
            if provider is None:
                self._states.clear()
            else:
                self._states.pop(provider, None)


default_circuit_breaker = CircuitBreaker()
