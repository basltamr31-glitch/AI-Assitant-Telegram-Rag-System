"""Tests for the per-user rate limit.

The clock is injected, so "ten minutes later" is an assignment rather than a
wait, and the tests run in microseconds.
"""

from __future__ import annotations

from app.api.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_events_within_the_limit_are_allowed_and_the_next_is_not() -> None:
    limiter = SlidingWindowLimiter(3, 60, clock=Clock())
    assert [limiter.allow(1) for _ in range(4)] == [True, True, True, False]


def test_the_window_slides() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, 60, clock=clock)
    limiter.allow(1)
    clock.now += 30
    limiter.allow(1)
    assert limiter.allow(1) is False

    clock.now += 31  # the first event is now 61 s old
    assert limiter.allow(1) is True


def test_refused_attempts_do_not_extend_the_wait() -> None:
    """Retrying while limited must not keep pushing the user back."""
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 60, clock=clock)
    limiter.allow(1)
    for _ in range(50):
        clock.now += 1
        limiter.allow(1)
    clock.now += 10  # 60 s after the only allowed event
    assert limiter.allow(1) is True


def test_users_are_limited_separately() -> None:
    limiter = SlidingWindowLimiter(1, 60, clock=Clock())
    assert limiter.allow(1) and limiter.allow(2)
    assert not limiter.allow(1)


def test_retry_after_says_how_long() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 600, clock=clock)
    assert limiter.retry_after(1) == 0
    limiter.allow(1)
    clock.now += 100
    assert limiter.retry_after(1) == 500
