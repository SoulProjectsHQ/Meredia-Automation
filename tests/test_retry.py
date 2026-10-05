import inspect
import time
import unittest

from src.automation.errors import RETRYABLE, AutomationError, ErrorType
from src.automation.retry import RateLimiter, retry_call


def temporary(message: str = "try again") -> AutomationError:
    return AutomationError(ErrorType.TEMPORARY_ERROR, message)


class Scripted:
    """Callable that plays back a list of outcomes. Exceptions are raised, other values returned."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSleep:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeClock:
    """Manual clock. sleep() moves time forward like a real sleep would."""

    def __init__(self, start: float = 100.0):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RetrySuccessTests(unittest.TestCase):
    def test_success_on_first_try(self):
        fn, sleep = Scripted("ok"), FakeSleep()
        self.assertEqual(retry_call(fn, sleep=sleep), "ok")
        self.assertEqual(fn.calls, 1)
        self.assertEqual(sleep.delays, [])

    def test_falsy_result_is_returned(self):
        for value in (None, 0, "", []):
            with self.subTest(value=value):
                self.assertEqual(retry_call(Scripted(value), sleep=FakeSleep()), value)

    def test_retry_then_success(self):
        fn, sleep = Scripted(temporary(), temporary(), "ok"), FakeSleep()
        self.assertEqual(retry_call(fn, sleep=sleep), "ok")
        self.assertEqual(fn.calls, 3)
        self.assertEqual(sleep.delays, [1.0, 2.0])

    def test_success_on_last_allowed_attempt(self):
        fn, sleep = Scripted(temporary(), temporary(), temporary(), "ok"), FakeSleep()
        self.assertEqual(retry_call(fn, attempts=4, sleep=sleep), "ok")
        self.assertEqual(fn.calls, 4)
        self.assertEqual(len(sleep.delays), 3)


class RetryFailureTests(unittest.TestCase):
    def test_attempts_exhausted_raises_last_error(self):
        errors = [temporary("first"), temporary("second"), temporary("third")]
        fn, sleep = Scripted(*errors), FakeSleep()
        with self.assertRaises(AutomationError) as caught:
            retry_call(fn, attempts=3, sleep=sleep)
        self.assertIs(caught.exception, errors[-1])
        self.assertEqual(fn.calls, 3)
        self.assertEqual(len(sleep.delays), 2)

    def test_single_attempt_never_retries_or_sleeps(self):
        error = temporary()
        fn, sleep = Scripted(error), FakeSleep()
        with self.assertRaises(AutomationError) as caught:
            retry_call(fn, attempts=1, sleep=sleep)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.calls, 1)
        self.assertEqual(sleep.delays, [])

    def test_non_retryable_is_not_retried(self):
        error = AutomationError(ErrorType.VALIDATION_ERROR, "bad input")
        fn, sleep = Scripted(error, "never reached"), FakeSleep()
        with self.assertRaises(AutomationError) as caught:
            retry_call(fn, sleep=sleep)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.calls, 1)
        self.assertEqual(sleep.delays, [])

    def test_non_retryable_after_retryable_stops_at_once(self):
        fatal = AutomationError(ErrorType.AUTHENTICATION_ERROR, "token expired")
        fn, sleep = Scripted(temporary(), fatal, "never reached"), FakeSleep()
        with self.assertRaises(AutomationError) as caught:
            retry_call(fn, sleep=sleep)
        self.assertIs(caught.exception, fatal)
        self.assertEqual(fn.calls, 2)
        self.assertEqual(sleep.delays, [1.0])

    def test_every_error_type_follows_its_retryable_flag(self):
        attempts = 3
        for error_type in ErrorType:
            with self.subTest(error_type=error_type):
                error = AutomationError(error_type, "x")
                fn = Scripted(*[error] * attempts)
                sleep = FakeSleep()
                with self.assertRaises(AutomationError) as caught:
                    retry_call(fn, attempts=attempts, sleep=sleep)
                self.assertIs(caught.exception, error)
                expected_calls = attempts if error.retryable else 1
                self.assertEqual(fn.calls, expected_calls)
                self.assertEqual(len(sleep.delays), expected_calls - 1)

    def test_retryable_set_matches_flag(self):
        for error_type in ErrorType:
            with self.subTest(error_type=error_type):
                self.assertEqual(AutomationError(error_type, "x").retryable, error_type in RETRYABLE)

    def test_other_exceptions_propagate_at_once(self):
        for exc in (ValueError("v"), KeyError("k"), RuntimeError("r"), OSError("o")):
            with self.subTest(exc=type(exc).__name__):
                fn, sleep = Scripted(exc, "never reached"), FakeSleep()
                with self.assertRaises(type(exc)) as caught:
                    retry_call(fn, sleep=sleep)
                self.assertIs(caught.exception, exc)
                self.assertEqual(fn.calls, 1)
                self.assertEqual(sleep.delays, [])

    def test_other_exception_after_retryable_stops_at_once(self):
        boom = RuntimeError("boom")
        fn, sleep = Scripted(temporary(), boom, "never reached"), FakeSleep()
        with self.assertRaises(RuntimeError) as caught:
            retry_call(fn, sleep=sleep)
        self.assertIs(caught.exception, boom)
        self.assertEqual(fn.calls, 2)
        self.assertEqual(sleep.delays, [1.0])


class RetryDelayTests(unittest.TestCase):
    def run_failing(self, attempts: int, **kwargs) -> list[float]:
        fn, sleep = Scripted(*[temporary() for _ in range(attempts)]), FakeSleep()
        with self.assertRaises(AutomationError):
            retry_call(fn, attempts=attempts, sleep=sleep, **kwargs)
        return sleep.delays

    def test_default_delays_double_from_one_second(self):
        self.assertEqual(self.run_failing(5), [1.0, 2.0, 4.0, 8.0])

    def test_max_delay_caps_the_sequence(self):
        delays = self.run_failing(7, base_delay=1.0, max_delay=5.0)
        self.assertEqual(delays, [1.0, 2.0, 4.0, 5.0, 5.0, 5.0])

    def test_default_cap_is_thirty_seconds(self):
        delays = self.run_failing(9, base_delay=0.5)
        self.assertEqual(delays, [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0])

    def test_base_delay_above_cap_is_capped_from_the_start(self):
        self.assertEqual(self.run_failing(3, base_delay=10.0, max_delay=3.0), [3.0, 3.0])

    def test_zero_base_delay_sleeps_zero(self):
        self.assertEqual(self.run_failing(3, base_delay=0.0), [0.0, 0.0])

    def test_zero_max_delay_sleeps_zero(self):
        self.assertEqual(self.run_failing(3, max_delay=0.0), [0.0, 0.0])

    def test_no_sleep_after_the_last_attempt(self):
        self.assertEqual(len(self.run_failing(4)), 3)


class RetryOnRetryTests(unittest.TestCase):
    def test_on_retry_gets_attempt_error_and_delay(self):
        first, second, third = temporary("a"), temporary("b"), temporary("c")
        fn, sleep, seen = Scripted(first, second, third), FakeSleep(), []
        with self.assertRaises(AutomationError):
            retry_call(fn, attempts=3, sleep=sleep, on_retry=lambda *args: seen.append(args))
        self.assertEqual(seen, [(1, first, 1.0), (2, second, 2.0)])

    def test_on_retry_runs_before_sleep(self):
        events: list[str] = []
        fn = Scripted(temporary(), "ok")
        retry_call(
            fn,
            sleep=lambda seconds: events.append(f"sleep {seconds}"),
            on_retry=lambda attempt, error, delay: events.append(f"retry {attempt} {delay}"),
        )
        self.assertEqual(events, ["retry 1 1.0", "sleep 1.0"])

    def test_on_retry_not_called_on_success(self):
        seen: list = []
        retry_call(Scripted("ok"), sleep=FakeSleep(), on_retry=lambda *args: seen.append(args))
        self.assertEqual(seen, [])

    def test_on_retry_not_called_for_non_retryable(self):
        seen: list = []
        fn = Scripted(AutomationError(ErrorType.INTERNAL_ERROR, "bug"))
        with self.assertRaises(AutomationError):
            retry_call(fn, sleep=FakeSleep(), on_retry=lambda *args: seen.append(args))
        self.assertEqual(seen, [])

    def test_on_retry_not_called_after_the_last_attempt(self):
        seen: list = []
        fn = Scripted(temporary(), temporary())
        with self.assertRaises(AutomationError):
            retry_call(fn, attempts=2, sleep=FakeSleep(), on_retry=lambda *args: seen.append(args))
        self.assertEqual(len(seen), 1)


class RetryArgumentTests(unittest.TestCase):
    def assert_rejected(self, **kwargs):
        fn = Scripted("never reached")
        with self.assertRaises(ValueError):
            retry_call(fn, sleep=FakeSleep(), **kwargs)
        self.assertEqual(fn.calls, 0)

    def test_attempts_below_one_is_rejected(self):
        for attempts in (0, -1, -10):
            with self.subTest(attempts=attempts):
                self.assert_rejected(attempts=attempts)

    def test_non_integer_attempts_is_rejected(self):
        for attempts in (2.5, "3", None):
            with self.subTest(attempts=attempts):
                self.assert_rejected(attempts=attempts)

    def test_negative_base_delay_is_rejected(self):
        self.assert_rejected(base_delay=-0.1)

    def test_negative_max_delay_is_rejected(self):
        self.assert_rejected(max_delay=-1.0)

    def test_nan_delays_are_rejected(self):
        self.assert_rejected(base_delay=float("nan"))
        self.assert_rejected(max_delay=float("nan"))

    def test_real_sleep_is_the_default(self):
        params = inspect.signature(retry_call).parameters
        self.assertIs(params["sleep"].default, time.sleep)
        self.assertEqual(params["attempts"].default, 3)
        self.assertEqual(params["base_delay"].default, 1.0)
        self.assertEqual(params["max_delay"].default, 30.0)
        self.assertIsNone(params["on_retry"].default)


class RateLimiterTests(unittest.TestCase):
    def make(self, min_interval: float) -> tuple[RateLimiter, FakeClock]:
        clock = FakeClock()
        return RateLimiter(min_interval, clock=clock, sleep=clock.sleep), clock

    def test_first_call_does_not_sleep(self):
        limiter, clock = self.make(2.0)
        limiter.wait()
        self.assertEqual(clock.slept, [])

    def test_immediate_second_call_sleeps_full_interval(self):
        limiter, clock = self.make(2.0)
        limiter.wait()
        limiter.wait()
        self.assertEqual(clock.slept, [2.0])

    def test_second_call_sleeps_only_the_remainder(self):
        limiter, clock = self.make(2.0)
        limiter.wait()
        clock.advance(0.5)
        limiter.wait()
        self.assertEqual(clock.slept, [1.5])

    def test_no_sleep_when_interval_has_passed(self):
        limiter, clock = self.make(2.0)
        limiter.wait()
        clock.advance(2.0)
        limiter.wait()
        clock.advance(5.0)
        limiter.wait()
        self.assertEqual(clock.slept, [])

    def test_calls_are_at_least_min_interval_apart(self):
        limiter, clock = self.make(2.0)
        work_times = [0.0, 0.5, 3.0, 0.0, 1.0, 0.25, 2.0]
        stamps = []
        for work in work_times:
            limiter.wait()
            stamps.append(clock.now)
            clock.advance(work)
        self.assertEqual(len(stamps), len(work_times))
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(later - earlier, 2.0)

    def test_zero_interval_never_sleeps(self):
        limiter, clock = self.make(0)
        for _ in range(5):
            limiter.wait()
        self.assertEqual(clock.slept, [])

    def test_negative_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(-1.0)
        with self.assertRaises(ValueError):
            RateLimiter(-0.001)

    def test_nan_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(float("nan"))

    def test_limiters_do_not_share_state(self):
        clock = FakeClock()
        a = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
        b = RateLimiter(2.0, clock=clock, sleep=clock.sleep)
        a.wait()
        b.wait()
        self.assertEqual(clock.slept, [])

    def test_real_clock_and_sleep_are_the_defaults(self):
        params = inspect.signature(RateLimiter).parameters
        self.assertIs(params["clock"].default, time.monotonic)
        self.assertIs(params["sleep"].default, time.sleep)


if __name__ == "__main__":
    unittest.main()
