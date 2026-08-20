from infra.circuit_breaker import CircuitBreaker


def _clock(start=0.0):
    """A controllable fake clock - a list with one mutable element, so the
    test can advance time without real sleeping."""
    return [start]


def test_circuit_starts_closed():
    cb = CircuitBreaker()
    assert cb.allow_call("apollo") is True


def test_stays_closed_below_the_failure_threshold():
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        cb.record_failure("apollo")
    assert cb.allow_call("apollo") is True


def test_opens_at_the_failure_threshold():
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(5):
        cb.record_failure("apollo")
    assert cb.allow_call("apollo") is False


def test_a_success_fully_resets_the_failure_count():
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        cb.record_failure("apollo")
    cb.record_success("apollo")
    for _ in range(4):
        cb.record_failure("apollo")
    assert cb.allow_call("apollo") is True  # only 4 failures since the reset


def test_providers_are_tracked_independently():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure("apollo")
    assert cb.allow_call("apollo") is False
    assert cb.allow_call("anthropic") is True


def test_allows_one_trial_call_after_cooldown_elapses():
    clock = _clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0, clock=lambda: clock[0])

    cb.record_failure("apollo")
    assert cb.allow_call("apollo") is False  # still within cooldown

    clock[0] += 61.0
    assert cb.allow_call("apollo") is True  # cooldown elapsed - one trial call allowed


def test_a_second_caller_cannot_sneak_in_during_the_one_trial_call():
    clock = _clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0, clock=lambda: clock[0])
    cb.record_failure("apollo")
    clock[0] += 61.0

    assert cb.allow_call("apollo") is True  # first caller gets the trial
    assert cb.allow_call("apollo") is False  # second caller, same window, is blocked


def test_a_successful_trial_call_closes_the_circuit():
    clock = _clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0, clock=lambda: clock[0])
    cb.record_failure("apollo")
    cb.record_failure("apollo")
    clock[0] += 61.0
    cb.allow_call("apollo")  # consume the trial

    cb.record_success("apollo")

    assert cb.allow_call("apollo") is True
    # a full reset means it takes a fresh failure_threshold worth of
    # failures to reopen, not just one
    cb.record_failure("apollo")
    assert cb.allow_call("apollo") is True


def test_a_failed_trial_call_reopens_the_circuit_for_another_cooldown():
    clock = _clock()
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0, clock=lambda: clock[0])
    cb.record_failure("apollo")
    clock[0] += 61.0
    cb.allow_call("apollo")  # consume the trial

    cb.record_failure("apollo")  # trial failed

    assert cb.allow_call("apollo") is False  # reopened, not immediately retryable
    clock[0] += 61.0
    assert cb.allow_call("apollo") is True  # but another cooldown later, trial-able again


def test_reset_clears_a_single_provider():
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure("apollo")
    cb.record_failure("anthropic")

    cb.reset("apollo")

    assert cb.allow_call("apollo") is True
    assert cb.allow_call("anthropic") is False


def test_reset_with_no_argument_clears_every_provider():
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure("apollo")
    cb.record_failure("anthropic")

    cb.reset()

    assert cb.allow_call("apollo") is True
    assert cb.allow_call("anthropic") is True
