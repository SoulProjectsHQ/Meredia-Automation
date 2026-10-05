"""Retry with exponential backoff and a simple rate limiter for outgoing calls.

Only AutomationError with retryable True is retried (temporary, rate limit and
external service errors). Validation, authentication and internal errors stop
the run at once, and so does any exception that is not an AutomationError.

Sleep and clock are injectable so tests never wait for real time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from src.automation.errors import AutomationError

T = TypeVar("T")


def _require_non_negative(name: str, value: float) -> None:
    # "not value >= 0" also rejects NaN.
    if not value >= 0:
        raise ValueError(f"{name} cannot be negative, got {value!r}")


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, AutomationError, float], None] | None = None,
) -> T:
    """Call fn() and return its result, retrying retryable AutomationError.

    At most `attempts` calls are made in total. After failed attempt n (counting
    from 1) the call sleeps min(max_delay, base_delay * 2 ** (n - 1)) seconds.
    on_retry(attempt, error, delay) is called before each sleep. When the last
    attempt fails, its error is raised.
    """
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be an integer of at least 1, got {attempts!r}")
    _require_non_negative("base_delay", base_delay)
    _require_non_negative("max_delay", max_delay)

    attempt = 1
    while True:
        try:
            return fn()
        except AutomationError as error:
            if not error.retryable or attempt >= attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            if on_retry is not None:
                on_retry(attempt, error, delay)
            sleep(delay)
        attempt += 1


class RateLimiter:
    """Keeps successive wait() calls at least min_interval seconds apart.

    The first call never sleeps. A min_interval of 0 never sleeps. Not thread safe.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _require_non_negative("min_interval", min_interval)
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    def wait(self) -> None:
        """Sleep if the previous call was less than min_interval seconds ago."""
        if self.min_interval > 0 and self._last_call is not None:
            remaining = self._last_call + self.min_interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = self._clock()
