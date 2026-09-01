import random

from qcom.core.config import RetryCfg, RetryEntry
from qcom.core.errors import ErrorCode
from qcom.core.retry import CircuitBreaker, RetryPolicy
from qcom.core.throttle import HostThrottle


def test_policy_table():
    p = RetryPolicy(RetryCfg(), rng=random.Random(0))
    assert p.max_attempts(ErrorCode.NETWORK_TIMEOUT) == 3
    assert p.should_retry(ErrorCode.NETWORK_TIMEOUT, 2) and not p.should_retry(ErrorCode.NETWORK_TIMEOUT, 3)
    for code in (ErrorCode.BLOCKED, ErrorCode.SCHEMA_DRIFT, ErrorCode.PARSE_ERROR, ErrorCode.NO_RESULTS):
        assert not p.should_retry(code, 0) and p.max_attempts(code) == 1 and p.backoff_seconds(code, 1) == 0
    assert p.should_retry(ErrorCode.UNKNOWN, 0) and not p.should_retry(ErrorCode.UNKNOWN, 1)


def test_backoff_grows_exponentially_with_jitter():
    cfg = RetryCfg(network_timeout=RetryEntry(attempts=3, backoff_base_s=2, jitter_s=1))
    p = RetryPolicy(cfg, rng=random.Random(1))
    b1, b2 = p.backoff_seconds(ErrorCode.NETWORK_TIMEOUT, 1), p.backoff_seconds(ErrorCode.NETWORK_TIMEOUT, 2)
    assert 2 <= b1 <= 3 and 4 <= b2 <= 5
    assert p.backoff_seconds(ErrorCode.RATE_LIMITED, 1) >= 60


def test_circuit_breaker_trips_once_at_threshold_and_resets_on_success():
    b = CircuitBreaker(3)
    assert not b.record(failed=True) and not b.record(failed=True)
    assert not b.record(failed=False) and b.consecutive == 0
    assert not b.record(failed=True) and not b.record(failed=True)
    assert b.record(failed=True) is True and b.open
    assert b.record(failed=True) is False  # already open, no second trip


def test_throttle_spaces_requests_per_host():
    clock = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock[0] += s

    t = HostThrottle(8, 0, sleep=sleep, clock=lambda: clock[0], rng=random.Random(0))
    assert t.wait("a") == 0
    assert t.wait("b") == 0  # different host, no wait
    assert t.wait("a") == 8
    clock[0] += 3
    assert abs(t.wait("a") - 5) < 1e-9
    assert sum(slept) == 13
