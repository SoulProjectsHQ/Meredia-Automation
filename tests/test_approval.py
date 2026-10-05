import unittest

from src.crm.approval import NON_HUMAN_APPROVERS, require_human_approver


class ApprovalTests(unittest.TestCase):
    def test_named_human_is_accepted_and_trimmed(self):
        self.assertEqual(require_human_approver("  Aleksander "), "Aleksander")

    def test_non_humans_and_blanks_are_rejected(self):
        for who in [*NON_HUMAN_APPROVERS, "AI", "Claude", "", "   ", None]:
            with self.subTest(who=who):
                with self.assertRaises(ValueError):
                    require_human_approver(who)


if __name__ == "__main__":
    unittest.main()
