"""Suppression list: what a deleted DO_NOT_CONTACT lead leaves behind, and what it blocks."""

import sqlite3
import unittest
from datetime import datetime

from src.crm.models import InteractionKind, Lead, LeadStatus, ResearchSource
from src.crm.repository import DuplicateLeadError, SuppressedLeadError
from tests.test_repository import new_repo, sample_lead
from tests.test_validation import make_org_number, valid_org_number

NOW = datetime(2026, 10, 6, 9, 30)
S = LeadStatus


def other_org_number() -> str:
    """A second synthetic valid org number, different from valid_org_number()."""
    for n in range(91234600, 91234700):
        candidate = make_org_number(str(n))
        if candidate and candidate != valid_org_number():
            return candidate
    raise AssertionError("no second valid synthetic org number found")


class SuppressionTestCase(unittest.TestCase):
    """A lead that is DO_NOT_CONTACT, with the usual related rows."""

    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead(notes="Ba om ikke mer kontakt"))
        self.repo.add_interaction(self.lead_id, InteractionKind.NOTE, "Ringte Ola", NOW)
        self.repo.add_task(self.lead_id, "oppgave")
        self.repo.add_research_source(self.lead_id, ResearchSource("https://eksempel.no"))
        self.repo.change_status(self.lead_id, S.DO_NOT_CONTACT)

    def count(self, table):
        return self.repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def delete(self, who="Aleksander"):
        return self.repo.delete_lead(self.lead_id, who, now=NOW)


class DeleteDoNotContactTests(SuppressionTestCase):
    def test_delete_writes_the_normalized_identifiers_and_the_approver(self):
        result = self.delete()
        self.assertTrue(result.suppressed)
        self.assertTrue(result.company_deleted)
        self.assertEqual(result.confirmed_by, "Aleksander")
        [entry] = self.repo.list_suppressions()
        self.assertEqual(entry.organization_number, valid_org_number())
        self.assertEqual(entry.domain, "eksempel.no")
        self.assertEqual(entry.name_key, "eksempel regnskap")
        self.assertEqual(entry.confirmed_by, "Aleksander")
        self.assertIsInstance(entry.created_at, datetime)

    def test_identifiers_are_normalized_before_they_are_stored(self):
        repo = new_repo()
        lead_id = repo.add_lead(
            sample_lead(
                company_name="  Nordlys A/S ",
                organization_number=f"NO {valid_org_number()} MVA",
                website="HTTP://WWW.Nordlys.NO/om-oss",
            )
        )
        repo.change_status(lead_id, S.DO_NOT_CONTACT)
        repo.delete_lead(lead_id, "Aleksander")
        [entry] = repo.list_suppressions()
        self.assertEqual(
            (entry.organization_number, entry.domain, entry.name_key), (valid_org_number(), "nordlys.no", "nordlys")
        )

    def test_lead_and_everything_around_it_is_deleted(self):
        self.delete()
        for table in ("lead", "company", "contact", "interaction", "task", "research_source", "email"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count("suppression"), 1)

    def test_company_stays_if_it_has_another_lead_but_the_suppression_is_written(self):
        company_id = self.repo.get_lead(self.lead_id).company_id
        self.repo.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company_id,))
        result = self.delete()
        self.assertTrue(result.suppressed)
        self.assertFalse(result.company_deleted)
        self.assertEqual(self.count("company"), 1)
        self.assertEqual(len(self.repo.list_suppressions()), 1)

    def test_delete_still_needs_a_named_human(self):
        for who in ("", "  ", "ai", "system", "Claude", None):
            with self.subTest(who=who):
                with self.assertRaises(ValueError):
                    self.delete(who)
        self.assertEqual(self.count("lead"), 1)
        self.assertEqual(self.repo.list_suppressions(), [])

    def test_delete_of_a_lead_that_is_not_do_not_contact_makes_no_suppression(self):
        for status in (S.NEW, S.RESEARCHED, S.LOST):
            with self.subTest(status=status):
                repo = new_repo()
                lead_id = repo.add_lead(sample_lead())
                if status == S.RESEARCHED:
                    repo.change_status(lead_id, S.RESEARCHED)
                elif status == S.LOST:
                    repo.change_status(lead_id, S.LOST)
                result = repo.delete_lead(lead_id, "Aleksander")
                self.assertFalse(result.suppressed)
                self.assertEqual(repo.list_suppressions(), [])
                repo.add_lead(sample_lead())  # nothing blocks the company

    def test_deletion_is_refused_without_any_identifier(self):
        # "AS" is only a legal suffix, so there is no usable name key either
        repo = new_repo()
        lead_id = repo.add_lead(sample_lead(company_name="AS", organization_number=None, website=None))
        repo.add_interaction(lead_id, InteractionKind.NOTE, "x", NOW)
        repo.change_status(lead_id, S.DO_NOT_CONTACT)
        with self.assertRaises(ValueError):
            repo.delete_lead(lead_id, "Aleksander")
        self.assertEqual(len(repo.list_leads()), 1)
        self.assertEqual(len(repo.list_interactions(lead_id)), 1)
        self.assertEqual(repo.list_suppressions(), [])

    def test_lead_without_identifiers_can_be_deleted_when_it_is_not_do_not_contact(self):
        repo = new_repo()
        lead_id = repo.add_lead(sample_lead(company_name="AS", organization_number=None, website=None))
        self.assertFalse(repo.delete_lead(lead_id, "Aleksander").suppressed)
        self.assertEqual(repo.list_leads(), [])


class SuppressionBlocksTests(SuppressionTestCase):
    def setUp(self):
        super().setUp()
        self.delete()

    def assertBlocked(self, lead, matched_on):
        with self.assertRaises(SuppressedLeadError) as ctx:
            self.repo.add_lead(lead)
        self.assertEqual(ctx.exception.matched_on, matched_on)
        self.assertNotIsInstance(ctx.exception, DuplicateLeadError)
        self.assertEqual(self.repo.list_leads(), [])
        self.assertEqual(self.count("company"), 0)

    def test_blocks_add_by_organization_number(self):
        self.assertBlocked(
            sample_lead(company_name="Helt annet navn", website="https://annet.no"), "organization_number"
        )

    def test_blocks_add_by_organization_number_in_another_format(self):
        self.assertBlocked(
            sample_lead(
                company_name="Helt annet navn", organization_number=f"NO {valid_org_number()} MVA", website=None
            ),
            "organization_number",
        )

    def test_blocks_add_by_domain(self):
        self.assertBlocked(
            sample_lead(
                company_name="Helt annet navn",
                organization_number=other_org_number(),
                website="http://www.eksempel.no/kontakt",
            ),
            "domain",
        )

    def test_blocks_add_by_name(self):
        self.assertBlocked(
            sample_lead(
                company_name="EKSEMPEL regnskap", organization_number=other_org_number(), website="https://annet.no"
            ),
            "company_name",
        )

    def test_org_number_is_checked_before_domain_and_domain_before_name(self):
        self.assertBlocked(sample_lead(), "organization_number")
        self.assertBlocked(sample_lead(organization_number=other_org_number()), "domain")
        self.assertBlocked(sample_lead(organization_number=other_org_number(), website=None), "company_name")

    def test_unrelated_company_is_not_blocked(self):
        lead_id = self.repo.add_lead(
            Lead(company_name="Helt Annet AS", organization_number=other_org_number(), website="https://annet.no")
        )
        self.assertEqual(self.repo.get_lead(lead_id).lead.company_name, "Helt Annet AS")

    def test_update_into_a_suppressed_identity_is_blocked(self):
        other_id = self.repo.add_lead(Lead(company_name="Gamma AS", website="https://gamma.no"))
        cases = (
            ({"organization_number": valid_org_number()}, "organization_number"),
            ({"website": "https://www.eksempel.no/om-oss"}, "domain"),
            ({"company_name": "Eksempel Regnskap"}, "company_name"),
        )
        for changes, matched_on in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(SuppressedLeadError) as ctx:
                    self.repo.update_lead(other_id, changes)
                self.assertEqual(ctx.exception.matched_on, matched_on)
        stored = self.repo.get_lead(other_id).lead
        self.assertEqual(
            (stored.company_name, stored.organization_number, stored.website), ("Gamma AS", None, "https://gamma.no")
        )

    def test_update_that_does_not_touch_identity_is_not_blocked(self):
        other_id = self.repo.add_lead(Lead(company_name="Gamma AS", website="https://gamma.no"))
        stored = self.repo.update_lead(other_id, {"industry": "Advokat", "website": "https://www.gamma.no"})
        self.assertEqual(stored.lead.industry, "Advokat")

    def test_remaining_lead_of_the_same_company_can_still_be_edited(self):
        repo = new_repo()
        first = repo.add_lead(sample_lead())
        company_id = repo.get_lead(first).company_id
        second = repo.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company_id,)).lastrowid
        repo.change_status(first, S.DO_NOT_CONTACT)
        repo.delete_lead(first, "Aleksander")
        stored = repo.update_lead(second, {"industry": "Advokat", "website": "https://eksempel.no"})
        self.assertEqual(stored.lead.industry, "Advokat")


class SuppressionContentTests(SuppressionTestCase):
    def test_table_holds_identifiers_only(self):
        columns = {r["name"] for r in self.repo.conn.execute("PRAGMA table_info(suppression)")}
        self.assertEqual(columns, {"id", "organization_number", "domain", "name_key", "confirmed_by", "created_at"})

    def test_no_personal_data_is_copied_into_the_suppression(self):
        self.delete()
        row = self.repo.conn.execute("SELECT * FROM suppression").fetchone()
        stored = " | ".join(str(v) for v in tuple(row))
        for secret in (
            "Ola Nordmann",
            "Daglig leder",
            "post@eksempel.no",
            "Proff.no",
            "Ba om ikke mer kontakt",
            "Ringte Ola",
            "Nettsiden mangler tydelig kontaktpunkt",
            "Trondheim",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, stored)

    def test_list_is_empty_before_any_deletion(self):
        self.assertEqual(self.repo.list_suppressions(), [])


class SuppressionIdempotencyTests(SuppressionTestCase):
    def add_second_do_not_contact_lead(self):
        company_id = self.repo.get_lead(self.lead_id).company_id
        return self.repo.conn.execute(
            "INSERT INTO lead (company_id, status) VALUES (?, 'DO_NOT_CONTACT')", (company_id,)
        ).lastrowid

    def test_same_identifiers_are_not_inserted_twice(self):
        second = self.add_second_do_not_contact_lead()
        self.delete("Aleksander")
        result = self.repo.delete_lead(second, "Kari", now=NOW)
        self.assertTrue(result.suppressed)
        self.assertTrue(result.company_deleted)
        [entry] = self.repo.list_suppressions()
        self.assertEqual(entry.confirmed_by, "Aleksander")

    def test_existing_row_with_the_same_identifiers_is_left_alone(self):
        self.repo.conn.execute(
            "INSERT INTO suppression (organization_number, domain, name_key, confirmed_by)"
            " VALUES (?, 'eksempel.no', 'eksempel regnskap', 'Kari')",
            (valid_org_number(),),
        )
        self.assertTrue(self.delete().suppressed)
        [entry] = self.repo.list_suppressions()
        self.assertEqual(entry.confirmed_by, "Kari")

    def test_partly_overlapping_row_only_gets_the_missing_identifiers_added(self):
        self.repo.conn.execute(
            "INSERT INTO suppression (organization_number, confirmed_by) VALUES (?, 'Kari')", (valid_org_number(),)
        )
        self.assertTrue(self.delete().suppressed)
        first, second = self.repo.list_suppressions()
        self.assertEqual((first.organization_number, first.domain, first.name_key), (valid_org_number(), None, None))
        self.assertEqual(
            (second.organization_number, second.domain, second.name_key), (None, "eksempel.no", "eksempel regnskap")
        )
        self.assertEqual(second.confirmed_by, "Aleksander")
        with self.assertRaises(SuppressedLeadError):
            self.repo.add_lead(sample_lead(organization_number=None, website=None))


class SuppressionAtomicityTests(SuppressionTestCase):
    def assertNothingDeleted(self):
        for table in ("lead", "company", "contact", "interaction", "task", "research_source"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table), 1)
        self.assertEqual(self.repo.get_lead(self.lead_id).lead.lead_status, S.DO_NOT_CONTACT)

    def test_nothing_is_deleted_if_the_suppression_insert_fails(self):
        self.repo.conn.execute(
            "CREATE TRIGGER no_suppression BEFORE INSERT ON suppression BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            self.delete()
        self.assertNothingDeleted()
        self.assertEqual(self.repo.list_suppressions(), [])

    def test_no_suppression_is_left_if_the_delete_fails(self):
        self.repo.conn.execute(
            "CREATE TRIGGER no_company_delete BEFORE DELETE ON company BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            self.delete()
        self.assertNothingDeleted()
        self.assertEqual(self.repo.list_suppressions(), [])


if __name__ == "__main__":
    unittest.main()
