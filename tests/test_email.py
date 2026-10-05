import unittest
from datetime import date, datetime

from src.email.rules import check_cold_email, check_followup, word_count
from src.email.schedule import (
    automatic_followup_allowed,
    followup_1_date,
    followup_2_date,
    is_followup_due,
    is_preferred_send_day,
    next_send_day,
)
from src.email.status import EmailKind, EmailRecord, EmailStatus, approve, mark_sent, revert_to_draft

NOW = datetime(2026, 10, 6, 9, 30)


def draft() -> EmailRecord:
    return EmailRecord(lead_id=1, recipient="post@eksempel.no", kind=EmailKind.COLD, template_version="cold-email-v1")


class EmailStatusTests(unittest.TestCase):
    def test_new_record_is_draft(self):
        self.assertEqual(draft().status, EmailStatus.DRAFT)

    def test_human_approval_then_send(self):
        approved = approve(draft(), "Aleksander", NOW)
        self.assertEqual(approved.status, EmailStatus.APPROVED)
        self.assertEqual(approved.approved_by, "Aleksander")
        sent = mark_sent(approved, NOW, message_id="msg-1")
        self.assertEqual(sent.status, EmailStatus.SENT)
        self.assertEqual(sent.message_id, "msg-1")
        self.assertEqual(sent.sent_at, NOW)

    def test_cannot_send_a_draft(self):
        with self.assertRaises(ValueError):
            mark_sent(draft(), NOW)

    def test_ai_cannot_approve(self):
        for who in ("ai", "AI", "claude", "system", "automation", "", "  "):
            with self.subTest(who=who):
                with self.assertRaises(ValueError):
                    approve(draft(), who, NOW)

    def test_cannot_approve_twice_or_after_send(self):
        approved = approve(draft(), "Aleksander", NOW)
        with self.assertRaises(ValueError):
            approve(approved, "Aleksander", NOW)
        with self.assertRaises(ValueError):
            approve(mark_sent(approved, NOW), "Aleksander", NOW)

    def test_editing_voids_approval(self):
        reverted = revert_to_draft(approve(draft(), "Aleksander", NOW))
        self.assertEqual(reverted.status, EmailStatus.DRAFT)
        self.assertIsNone(reverted.approved_by)
        with self.assertRaises(ValueError):
            mark_sent(reverted, NOW)

    def test_sent_mail_cannot_be_reverted(self):
        sent = mark_sent(approve(draft(), "Aleksander", NOW), NOW)
        with self.assertRaises(ValueError):
            revert_to_draft(sent)


class ScheduleTests(unittest.TestCase):
    def test_preferred_days_are_tue_to_thu(self):
        # 2026-10-05 is a Monday
        flags = [is_preferred_send_day(date(2026, 10, 5 + i)) for i in range(7)]
        self.assertEqual(flags, [False, True, True, True, False, False, False])

    def test_next_send_day_rolls_forward(self):
        self.assertEqual(next_send_day(date(2026, 10, 6)), date(2026, 10, 6))  # Tuesday
        self.assertEqual(next_send_day(date(2026, 10, 9)), date(2026, 10, 13))  # Friday
        self.assertEqual(next_send_day(date(2026, 10, 11)), date(2026, 10, 13))  # Sunday
        self.assertEqual(next_send_day(date(2026, 10, 12)), date(2026, 10, 13))  # Monday

    def test_followup_1_is_about_four_days_after_and_on_a_send_day(self):
        # Thursday 2026-10-08 + 4 = Monday -> Tuesday 2026-10-13
        self.assertEqual(followup_1_date(date(2026, 10, 8)), date(2026, 10, 13))
        # Monday 2026-10-05 + 4 = Friday -> Tuesday 2026-10-13
        self.assertEqual(followup_1_date(date(2026, 10, 5)), date(2026, 10, 13))
        # Sunday 2026-10-04 + 4 = Thursday 2026-10-08
        self.assertEqual(followup_1_date(date(2026, 10, 4)), date(2026, 10, 8))

    def test_followup_2_is_seven_days_after_followup_1(self):
        self.assertEqual(followup_2_date(date(2026, 10, 13)), date(2026, 10, 20))
        self.assertEqual(followup_2_date(date(2026, 10, 15)), date(2026, 10, 22))

    def test_max_two_automatic_followups(self):
        self.assertTrue(automatic_followup_allowed(0))
        self.assertTrue(automatic_followup_allowed(1))
        self.assertFalse(automatic_followup_allowed(2))
        self.assertFalse(automatic_followup_allowed(3))
        with self.assertRaises(ValueError):
            automatic_followup_allowed(-1)

    def test_followup_due(self):
        today = date(2026, 10, 13)
        self.assertTrue(is_followup_due(date(2026, 10, 13), today))
        self.assertTrue(is_followup_due(date(2026, 10, 1), today))
        self.assertFalse(is_followup_due(date(2026, 10, 14), today))
        self.assertFalse(is_followup_due(None, today))


class RulesTests(unittest.TestCase):
    @staticmethod
    def words(n: int) -> str:
        return " ".join(["ord"] * n)

    def test_cold_email_word_limits(self):
        self.assertEqual(check_cold_email(self.words(70)), [])
        self.assertEqual(check_cold_email(self.words(140)), [])
        self.assertTrue(any("too short" in p for p in check_cold_email(self.words(69))))
        self.assertTrue(any("too long" in p for p in check_cold_email(self.words(141))))

    def test_banned_phrases_are_flagged_case_insensitively(self):
        body = self.words(80) + " Dette er en Revolusjonerende og unik løsning."
        problems = check_cold_email(body)
        self.assertIn("banned phrase: revolusjonerende", problems)
        self.assertIn("banned phrase: unik løsning", problems)

    def test_followup_must_be_shorter(self):
        first = self.words(100)
        self.assertEqual(check_followup(self.words(40), first), [])
        self.assertIn("follow-up must be shorter than the first mail", check_followup(self.words(100), first))

    def test_word_count(self):
        self.assertEqual(word_count("Hei  Ola,\nvi tar kontakt."), 5)


if __name__ == "__main__":
    unittest.main()
