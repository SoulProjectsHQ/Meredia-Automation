import unittest

from src.crm.models import LeadStatus
from src.crm.status import ALLOWED_TRANSITIONS, can_transition, is_contactable, transition

S = LeadStatus


class StatusTests(unittest.TestCase):
    def test_every_status_has_a_transition_entry(self):
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(LeadStatus))

    def test_happy_path(self):
        path = [S.NEW, S.RESEARCHED, S.QUALIFIED, S.READY_TO_CONTACT, S.CONTACTED,
                S.FOLLOWUP_1, S.FOLLOWUP_2, S.REPLIED, S.INTERESTED, S.MEETING, S.PROPOSAL, S.WON]
        for current, target in zip(path, path[1:]):
            with self.subTest(step=f"{current}->{target}"):
                self.assertEqual(transition(current, target), target)

    def test_cannot_skip_steps(self):
        self.assertFalse(can_transition(S.NEW, S.CONTACTED))
        self.assertFalse(can_transition(S.RESEARCHED, S.READY_TO_CONTACT))
        self.assertFalse(can_transition(S.QUALIFIED, S.CONTACTED))
        with self.assertRaises(ValueError):
            transition(S.NEW, S.WON)

    def test_reply_can_come_after_any_contact_stage(self):
        for stage in (S.CONTACTED, S.FOLLOWUP_1, S.FOLLOWUP_2):
            self.assertTrue(can_transition(stage, S.REPLIED))

    def test_open_statuses_can_be_lost_or_do_not_contact(self):
        open_statuses = set(LeadStatus) - {S.WON, S.LOST, S.DO_NOT_CONTACT}
        for status in open_statuses:
            with self.subTest(status=status):
                self.assertTrue(can_transition(status, S.LOST))
                self.assertTrue(can_transition(status, S.DO_NOT_CONTACT))

    def test_closed_statuses(self):
        self.assertEqual(ALLOWED_TRANSITIONS[S.WON], {S.DO_NOT_CONTACT})
        self.assertEqual(ALLOWED_TRANSITIONS[S.LOST], {S.DO_NOT_CONTACT})
        self.assertEqual(ALLOWED_TRANSITIONS[S.DO_NOT_CONTACT], frozenset())

    def test_no_backwards_moves(self):
        self.assertFalse(can_transition(S.CONTACTED, S.QUALIFIED))
        self.assertFalse(can_transition(S.LOST, S.NEW))

    def test_only_ready_to_contact_is_contactable(self):
        self.assertEqual([s for s in LeadStatus if is_contactable(s)], [S.READY_TO_CONTACT])


if __name__ == "__main__":
    unittest.main()
