import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.automation.errors import AutomationError, ErrorType
from src.automation.pipeline_log import PipelineLog, Stage, format_entry, redact


class ErrorTests(unittest.TestCase):
    def test_retryable_types(self):
        retryable = {ErrorType.TEMPORARY_ERROR, ErrorType.RATE_LIMIT, ErrorType.EXTERNAL_SERVICE_ERROR}
        for error_type in ErrorType:
            with self.subTest(error_type=error_type):
                self.assertEqual(AutomationError(error_type, "x").retryable, error_type in retryable)

    def test_message_includes_type(self):
        err = AutomationError(ErrorType.RATE_LIMIT, "429 from provider")
        self.assertEqual(str(err), "rate_limit: 429 from provider")


class LogTests(unittest.TestCase):
    WHEN = datetime(2026, 10, 5, 10, 31)

    def test_entry_format_matches_spec(self):
        self.assertEqual(
            format_entry(self.WHEN, Stage.DISCOVERY, "Found: Eksempel AS"),
            "2026-10-05 10:31\nDISCOVERY\nFound: Eksempel AS\n",
        )

    def test_multiline_message_becomes_one_line(self):
        self.assertEqual(format_entry(self.WHEN, Stage.RESEARCH, "a\n  b").splitlines()[2], "a b")

    def test_secrets_are_redacted(self):
        cases = {
            "Authorization: Bearer abc.def-123": "Bearer [REDACTED]",
            "api_key=sk-test1234567890": "api_key=[REDACTED]",
            "password: hunter2": "password: [REDACTED]",
            "using sk-abcdefghijkl as key": "using [REDACTED] as key",
        }
        for raw, expected_fragment in cases.items():
            with self.subTest(raw=raw):
                cleaned = redact(raw)
                self.assertIn(expected_fragment, cleaned)
                for secret in ("abc.def-123", "sk-test1234567890", "hunter2", "sk-abcdefghijkl"):
                    self.assertNotIn(secret, cleaned)

    def test_plain_text_is_untouched(self):
        self.assertEqual(redact("Score: 78"), "Score: 78")

    def test_logger_appends_entries_and_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "pipeline.log"
            log = PipelineLog(path)
            log.write(Stage.SCORING, "Score: 78", now=self.WHEN)
            log.write(Stage.ERROR, "token=abc123", now=self.WHEN)
            text = path.read_text(encoding="utf-8")
            self.assertIn("SCORING\nScore: 78\n", text)
            self.assertIn("token=[REDACTED]", text)
            self.assertNotIn("abc123", text)


if __name__ == "__main__":
    unittest.main()
