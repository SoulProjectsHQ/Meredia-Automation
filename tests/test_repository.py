import unittest
from datetime import date, datetime

from src.crm.db import connect, init_db
from src.crm.models import InteractionKind, Lead, LeadStatus, ResearchSource
from src.crm.repository import (
    DuplicateLeadError,
    LeadNotFoundError,
    LeadRepository,
    LeadValidationError,
)
from src.email.status import EmailKind, EmailRecord, EmailStatus, approve, mark_sent
from tests.test_validation import valid_org_number

NOW = datetime(2026, 10, 6, 9, 30)
S = LeadStatus


def new_repo() -> LeadRepository:
    conn = connect(":memory:")
    init_db(conn)
    return LeadRepository(conn)


def sample_lead(**overrides) -> Lead:
    data = dict(
        company_name="Eksempel Regnskap AS",
        organization_number=valid_org_number(),
        website="https://eksempel.no",
        industry="Regnskap",
        employee_count=12,
        location="Trondheim",
        contact_name="Ola Nordmann",
        contact_role="Daglig leder",
        contact_email="post@eksempel.no",
        contact_email_verified=True,
        source="Proff.no",
        lead_score=72,
        reason_for_fit="Passer storrelse og bransje",
        identified_problem="Nettsiden mangler tydelig kontaktpunkt",
        recommended_service="Ny nettside",
    )
    data.update(overrides)
    return Lead(**data)


class AddAndReadTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()

    def test_roundtrip_keeps_all_fields(self):
        lead = sample_lead(first_contact_date=date(2026, 10, 6), followup_1_date=date(2026, 10, 13), notes="Notat")
        lead_id = self.repo.add_lead(lead)
        stored = self.repo.get_lead(lead_id)
        self.assertEqual(stored.lead, lead)
        self.assertEqual(stored.lead_id, lead_id)

    def test_org_number_is_stored_normalized(self):
        org = valid_org_number()
        lead_id = self.repo.add_lead(sample_lead(organization_number=f"NO {org} MVA"))
        self.assertEqual(self.repo.get_lead(lead_id).lead.organization_number, org)

    def test_lead_without_contact_has_no_contact_row(self):
        lead_id = self.repo.add_lead(
            sample_lead(contact_name=None, contact_role=None, contact_email=None, contact_email_verified=False)
        )
        self.assertEqual(self.repo.conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0], 0)
        self.assertIsNone(self.repo.get_lead(lead_id).lead.contact_email)

    def test_invalid_lead_is_rejected_with_all_problems(self):
        with self.assertRaises(LeadValidationError) as ctx:
            self.repo.add_lead(sample_lead(organization_number="123", lead_score=500))
        self.assertEqual(len(ctx.exception.problems), 2)
        self.assertEqual(self.repo.list_leads(), [])

    def test_new_leads_must_start_as_new(self):
        with self.assertRaises(LeadValidationError):
            self.repo.add_lead(sample_lead(lead_status=S.QUALIFIED))

    def test_missing_lead(self):
        with self.assertRaises(LeadNotFoundError):
            self.repo.get_lead(999)

    def test_list_filters_by_status(self):
        first = self.repo.add_lead(sample_lead())
        self.repo.add_lead(sample_lead(company_name="Beta AS", organization_number=None, website="https://beta.no"))
        self.repo.change_status(first, S.RESEARCHED)
        self.assertEqual([s.lead_id for s in self.repo.list_leads(S.RESEARCHED)], [first])
        self.assertEqual(len(self.repo.list_leads(S.NEW)), 1)
        self.assertEqual(len(self.repo.list_leads()), 2)


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.first_id = self.repo.add_lead(sample_lead())

    def test_duplicate_org_number(self):
        with self.assertRaises(DuplicateLeadError) as ctx:
            self.repo.add_lead(sample_lead(company_name="Annet navn", website="https://annet.no"))
        self.assertEqual(ctx.exception.matched_on, "organization_number")
        self.assertEqual(ctx.exception.existing_lead_id, self.first_id)

    def test_duplicate_domain(self):
        with self.assertRaises(DuplicateLeadError) as ctx:
            self.repo.add_lead(
                sample_lead(company_name="Annet navn", organization_number=None, website="http://www.eksempel.no/kontakt")
            )
        self.assertEqual(ctx.exception.matched_on, "domain")

    def test_duplicate_name(self):
        with self.assertRaises(DuplicateLeadError) as ctx:
            self.repo.add_lead(
                sample_lead(company_name="EKSEMPEL regnskap", organization_number=None, website=None)
            )
        self.assertEqual(ctx.exception.matched_on, "company_name")

    def test_closed_leads_still_block_new_ones(self):
        self.repo.change_status(self.first_id, S.LOST)
        with self.assertRaises(DuplicateLeadError):
            self.repo.add_lead(sample_lead())

    def test_nothing_is_inserted_on_duplicate(self):
        with self.assertRaises(DuplicateLeadError):
            self.repo.add_lead(sample_lead())
        self.assertEqual(len(self.repo.list_leads()), 1)
        self.assertEqual(self.repo.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 1)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead())

    def test_update_fields_across_tables(self):
        stored = self.repo.update_lead(
            self.lead_id, {"industry": "Advokat", "contact_role": "Partner", "next_action": "Ring", "lead_score": 80}
        )
        self.assertEqual(stored.lead.industry, "Advokat")
        self.assertEqual(stored.lead.contact_role, "Partner")
        self.assertEqual(stored.lead.next_action, "Ring")
        self.assertEqual(stored.lead.lead_score, 80)

    def test_status_cannot_be_updated_here(self):
        with self.assertRaises(ValueError):
            self.repo.update_lead(self.lead_id, {"lead_status": S.WON})

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.update_lead(self.lead_id, {"favorite_color": "blue"})

    def test_invalid_update_is_rejected_and_nothing_changes(self):
        with self.assertRaises(LeadValidationError):
            self.repo.update_lead(self.lead_id, {"industry": "Advokat", "lead_score": 101})
        self.assertEqual(self.repo.get_lead(self.lead_id).lead.industry, "Regnskap")

    def test_changed_email_must_be_verified_again(self):
        stored = self.repo.update_lead(self.lead_id, {"contact_email": "ny@eksempel.no"})
        self.assertFalse(stored.lead.contact_email_verified)
        stored = self.repo.update_lead(self.lead_id, {"contact_email_verified": True})
        self.assertTrue(stored.lead.contact_email_verified)

    def test_same_email_keeps_verification(self):
        stored = self.repo.update_lead(self.lead_id, {"contact_email": "post@eksempel.no"})
        self.assertTrue(stored.lead.contact_email_verified)

    def test_contact_is_created_when_added_later(self):
        other = self.repo.add_lead(
            Lead(company_name="Gamma AS", website="https://gamma.no")
        )
        stored = self.repo.update_lead(other, {"contact_name": "Kari", "contact_email": "kari@gamma.no"})
        self.assertEqual(stored.lead.contact_name, "Kari")
        self.assertEqual(self.repo.conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0], 2)

    def test_cannot_update_into_a_duplicate(self):
        other = self.repo.add_lead(Lead(company_name="Gamma AS", website="https://gamma.no"))
        with self.assertRaises(DuplicateLeadError) as ctx:
            self.repo.update_lead(other, {"website": "https://eksempel.no"})
        self.assertEqual(ctx.exception.existing_lead_id, self.lead_id)

    def test_updating_own_identifiers_is_not_a_duplicate(self):
        self.repo.update_lead(self.lead_id, {"website": "https://www.eksempel.no", "company_name": "Eksempel Regnskap"})

    def test_ready_to_contact_cannot_lose_its_verified_email(self):
        self.repo.change_status(self.lead_id, S.RESEARCHED)
        self.repo.change_status(self.lead_id, S.QUALIFIED)
        self.repo.change_status(self.lead_id, S.READY_TO_CONTACT)
        with self.assertRaises(LeadValidationError):
            self.repo.update_lead(self.lead_id, {"contact_email": "ny@eksempel.no"})
        with self.assertRaises(LeadValidationError):
            self.repo.update_lead(self.lead_id, {"lead_score": None})


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()

    def test_happy_path_to_ready_to_contact(self):
        lead_id = self.repo.add_lead(sample_lead())
        for target in (S.RESEARCHED, S.QUALIFIED, S.READY_TO_CONTACT):
            self.assertEqual(self.repo.change_status(lead_id, target).lead.lead_status, target)

    def test_cannot_skip_steps(self):
        lead_id = self.repo.add_lead(sample_lead())
        with self.assertRaises(ValueError):
            self.repo.change_status(lead_id, S.CONTACTED)
        self.assertEqual(self.repo.get_lead(lead_id).lead.lead_status, S.NEW)

    def test_qualified_needs_a_score(self):
        lead_id = self.repo.add_lead(sample_lead(lead_score=None))
        self.repo.change_status(lead_id, S.RESEARCHED)
        with self.assertRaises(LeadValidationError):
            self.repo.change_status(lead_id, S.QUALIFIED)

    def test_ready_to_contact_needs_a_verified_email(self):
        lead_id = self.repo.add_lead(sample_lead(contact_email_verified=False))
        self.repo.change_status(lead_id, S.RESEARCHED)
        self.repo.change_status(lead_id, S.QUALIFIED)
        with self.assertRaises(LeadValidationError):
            self.repo.change_status(lead_id, S.READY_TO_CONTACT)
        self.repo.update_lead(lead_id, {"contact_email_verified": True})
        self.repo.change_status(lead_id, S.READY_TO_CONTACT)

    def test_do_not_contact_is_final(self):
        lead_id = self.repo.add_lead(sample_lead())
        self.repo.change_status(lead_id, S.DO_NOT_CONTACT)
        with self.assertRaises(ValueError):
            self.repo.change_status(lead_id, S.NEW)
        with self.assertRaises(ValueError):
            self.repo.change_status(lead_id, S.LOST)


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead())

    def last_contact(self):
        return self.repo.get_lead(self.lead_id).lead.last_contact_date

    def test_contact_moves_last_contact_date(self):
        self.repo.add_interaction(self.lead_id, InteractionKind.EMAIL_SENT, "Kald mail", NOW)
        self.assertEqual(self.last_contact(), date(2026, 10, 6))

    def test_note_does_not_move_last_contact_date(self):
        self.repo.add_interaction(self.lead_id, InteractionKind.NOTE, "Intern notis", NOW)
        self.assertIsNone(self.last_contact())

    def test_older_interaction_does_not_move_date_backwards(self):
        self.repo.add_interaction(self.lead_id, InteractionKind.REPLY, None, NOW)
        self.repo.add_interaction(self.lead_id, InteractionKind.CALL, None, datetime(2026, 10, 1, 8, 0))
        self.assertEqual(self.last_contact(), date(2026, 10, 6))
        self.assertEqual(len(self.repo.list_interactions(self.lead_id)), 2)

    def test_unknown_lead(self):
        with self.assertRaises(LeadNotFoundError):
            self.repo.add_interaction(999, InteractionKind.NOTE)


class EmailStorageTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead())
        self.record = EmailRecord(
            lead_id=self.lead_id, recipient="post@eksempel.no", kind=EmailKind.COLD, template_version="cold-email-v1"
        )

    def test_draft_approve_send_roundtrip(self):
        email_id = self.repo.save_email(self.record)
        approved = approve(self.record, "Aleksander", NOW)
        self.repo.update_email(email_id, approved)
        sent = mark_sent(approved, NOW, message_id="msg-1")
        self.repo.update_email(email_id, sent)
        [(stored_id, stored)] = self.repo.list_emails(self.lead_id)
        self.assertEqual(stored_id, email_id)
        self.assertEqual(stored, sent)
        self.assertEqual(stored.status, EmailStatus.SENT)

    def test_database_refuses_approved_mail_without_approver(self):
        import dataclasses
        import sqlite3

        fake = dataclasses.replace(self.record, status=EmailStatus.APPROVED)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_email(fake)

    def test_database_refuses_sent_mail_without_send_time(self):
        import dataclasses
        import sqlite3

        fake = dataclasses.replace(self.record, status=EmailStatus.SENT, approved_by="Aleksander")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_email(fake)

    def test_email_for_unknown_lead_is_rejected(self):
        import dataclasses

        with self.assertRaises(LeadNotFoundError):
            self.repo.save_email(dataclasses.replace(self.record, lead_id=999))

    def test_update_unknown_email(self):
        with self.assertRaises(LookupError):
            self.repo.update_email(999, self.record)


class TaskAndSourceTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead())

    def test_tasks(self):
        due = self.repo.add_task(self.lead_id, "Send oppfolging", date(2026, 10, 13))
        later = self.repo.add_task(self.lead_id, "Ring", date(2026, 10, 20))
        self.repo.add_task(self.lead_id, "Uten dato")
        self.assertEqual(len(self.repo.list_open_tasks()), 3)
        self.assertEqual([t["id"] for t in self.repo.list_open_tasks(date(2026, 10, 13))], [due])
        self.repo.complete_task(due)
        self.assertEqual(len(self.repo.list_open_tasks()), 2)
        self.assertIn(later, [t["id"] for t in self.repo.list_open_tasks(date(2026, 12, 1))])

    def test_task_needs_description(self):
        with self.assertRaises(LeadValidationError):
            self.repo.add_task(self.lead_id, "  ")

    def test_complete_unknown_task(self):
        with self.assertRaises(LookupError):
            self.repo.complete_task(999)

    def test_research_sources_are_traceable(self):
        self.repo.add_research_source(
            self.lead_id, ResearchSource("https://eksempel.no", "website", NOW, "Mangler HTTPS-omdirigering")
        )
        self.repo.add_research_source(self.lead_id, ResearchSource("https://proff.no/eksempel", "proff"))
        sources = self.repo.list_research_sources(self.lead_id)
        self.assertEqual([s.url for s in sources], ["https://eksempel.no", "https://proff.no/eksempel"])
        self.assertEqual(sources[0].retrieved_at, NOW)
        self.assertEqual(sources[0].note, "Mangler HTTPS-omdirigering")

    def test_research_source_needs_url(self):
        with self.assertRaises(LeadValidationError):
            self.repo.add_research_source(self.lead_id, ResearchSource(" "))


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.lead_id = self.repo.add_lead(sample_lead())
        self.repo.add_interaction(self.lead_id, InteractionKind.NOTE, "x")
        self.repo.add_task(self.lead_id, "oppgave")
        self.repo.add_research_source(self.lead_id, ResearchSource("https://eksempel.no"))

    def count(self, table):
        return self.repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_delete_needs_a_named_human(self):
        for who in ("", "  ", "ai", "system", "Claude", None):
            with self.subTest(who=who):
                with self.assertRaises(ValueError):
                    self.repo.delete_lead(self.lead_id, who)
        self.assertEqual(self.count("lead"), 1)

    def test_delete_removes_lead_company_and_related_rows(self):
        result = self.repo.delete_lead(self.lead_id, "Aleksander", now=NOW)
        self.assertTrue(result.company_deleted)
        self.assertEqual(result.confirmed_by, "Aleksander")
        self.assertEqual(result.deleted_at, NOW)
        for table in ("lead", "company", "contact", "interaction", "task", "research_source"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table), 0)

    def test_company_stays_if_it_has_another_lead(self):
        company_id = self.repo.get_lead(self.lead_id).company_id
        self.repo.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company_id,))
        result = self.repo.delete_lead(self.lead_id, "Aleksander")
        self.assertFalse(result.company_deleted)
        self.assertEqual(self.count("company"), 1)
        self.assertEqual(self.count("lead"), 1)

    def test_do_not_contact_is_never_deleted(self):
        self.repo.change_status(self.lead_id, S.DO_NOT_CONTACT)
        with self.assertRaises(ValueError):
            self.repo.delete_lead(self.lead_id, "Aleksander")
        self.assertEqual(self.count("lead"), 1)
        with self.assertRaises(DuplicateLeadError):
            self.repo.add_lead(sample_lead())

    def test_delete_unknown_lead(self):
        with self.assertRaises(LeadNotFoundError):
            self.repo.delete_lead(999, "Aleksander")


class ConnectionTests(unittest.TestCase):
    def test_requires_foreign_keys(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        with self.assertRaises(ValueError):
            LeadRepository(conn)


if __name__ == "__main__":
    unittest.main()
