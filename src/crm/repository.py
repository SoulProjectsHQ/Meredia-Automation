"""Lead repository on top of SQLite. All CRM reads and writes go through here.

Rules enforced here, not left to callers:
- new leads are validated, must start as NEW and are checked for duplicates first
- status changes only go through src.crm.status.transition
- deleting customer data needs a named human and never touches DO_NOT_CONTACT leads
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import date, datetime
from typing import Mapping

from src.crm.approval import require_human_approver
from src.crm.dedupe import find_duplicate
from src.crm.models import InteractionKind, Lead, LeadStatus, ResearchSource
from src.crm.status import transition
from src.crm.validation import normalize_domain, normalize_org_number, validate_lead
from src.email.status import EmailKind, EmailRecord, EmailStatus


class LeadValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class DuplicateLeadError(Exception):
    def __init__(self, matched_on: str, existing_lead_id: int) -> None:
        super().__init__(f"Duplicate lead (matched on {matched_on}), existing lead id {existing_lead_id}")
        self.matched_on = matched_on
        self.existing_lead_id = existing_lead_id


class LeadNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class StoredLead:
    lead_id: int
    company_id: int
    lead: Lead


@dataclass(frozen=True)
class DeletionResult:
    """Receipt for the caller to log. Holds ids only, no personal data."""

    lead_id: int
    company_deleted: bool
    confirmed_by: str
    deleted_at: datetime


_UPDATABLE_FIELDS = frozenset(f.name for f in fields(Lead)) - {"lead_status"}
_COMPANY_FIELDS = ("company_name", "organization_number", "website", "industry", "employee_count", "location")
_CONTACT_FIELDS = ("contact_name", "contact_role", "contact_email", "contact_email_verified")
_NO_CONTACT_KINDS = frozenset({InteractionKind.NOTE})

_SELECT_LEADS = """
SELECT l.id AS lead_id, l.company_id, l.status, l.score, l.source,
       l.reason_for_fit, l.identified_problem, l.recommended_service,
       l.first_contact_date, l.followup_1_date, l.followup_2_date, l.last_contact_date,
       l.next_action, l.notes,
       c.name AS company_name, c.organization_number, c.website, c.industry,
       c.employee_count, c.location,
       ct.name AS contact_name, ct.role AS contact_role, ct.email AS contact_email,
       ct.email_verified AS contact_email_verified
FROM lead l
JOIN company c ON c.id = l.company_id
LEFT JOIN contact ct ON ct.id = l.contact_id
"""


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


def _to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _to_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_stored(row: sqlite3.Row) -> StoredLead:
    lead = Lead(
        company_name=row["company_name"],
        organization_number=row["organization_number"],
        website=row["website"],
        industry=row["industry"],
        employee_count=row["employee_count"],
        location=row["location"],
        contact_name=row["contact_name"],
        contact_role=row["contact_role"],
        contact_email=row["contact_email"],
        contact_email_verified=bool(row["contact_email_verified"]),
        source=row["source"],
        lead_score=row["score"],
        lead_status=LeadStatus(row["status"]),
        reason_for_fit=row["reason_for_fit"],
        identified_problem=row["identified_problem"],
        recommended_service=row["recommended_service"],
        first_contact_date=_to_date(row["first_contact_date"]),
        followup_1_date=_to_date(row["followup_1_date"]),
        followup_2_date=_to_date(row["followup_2_date"]),
        last_contact_date=_to_date(row["last_contact_date"]),
        next_action=row["next_action"],
        notes=row["notes"],
    )
    return StoredLead(lead_id=row["lead_id"], company_id=row["company_id"], lead=lead)


def _status_problems(lead: Lead, status: LeadStatus) -> list[str]:
    """Data a lead must have to be in a given status. Plain checks, no LLM."""
    problems: list[str] = []
    if status in (LeadStatus.QUALIFIED, LeadStatus.READY_TO_CONTACT) and lead.lead_score is None:
        problems.append(f"lead_score is required for {status}")
    if status == LeadStatus.READY_TO_CONTACT and not (lead.contact_email and lead.contact_email_verified):
        problems.append("READY_TO_CONTACT requires a verified contact_email")
    return problems


class LeadRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ValueError("Connection must have foreign keys enabled, use src.crm.db.connect")
        conn.row_factory = sqlite3.Row
        self.conn = conn

    # -- leads -----------------------------------------------------------

    def add_lead(self, lead: Lead) -> int:
        """Insert a new lead with its company and contact. Returns the lead id."""
        problems = validate_lead(lead)
        if lead.lead_status != LeadStatus.NEW:
            problems.append("new leads must start as NEW")
        if problems:
            raise LeadValidationError(problems)

        stored = self.list_leads()
        match = find_duplicate(lead, [s.lead for s in stored])
        if match:
            existing_id = next(s.lead_id for s in stored if s.lead is match.existing)
            raise DuplicateLeadError(match.matched_on, existing_id)

        with self.conn:
            company_id = self.conn.execute(
                "INSERT INTO company (organization_number, name, website, domain, industry, employee_count, location)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    normalize_org_number(lead.organization_number),
                    lead.company_name.strip(),
                    lead.website,
                    normalize_domain(lead.website),
                    lead.industry,
                    lead.employee_count,
                    lead.location,
                ),
            ).lastrowid
            contact_id = self._insert_contact(company_id, lead)
            lead_id = self.conn.execute(
                "INSERT INTO lead (company_id, contact_id, status, score, source, reason_for_fit,"
                " identified_problem, recommended_service, first_contact_date, followup_1_date,"
                " followup_2_date, last_contact_date, next_action, notes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    company_id,
                    contact_id,
                    lead.lead_status.value,
                    lead.lead_score,
                    lead.source,
                    lead.reason_for_fit,
                    lead.identified_problem,
                    lead.recommended_service,
                    _iso(lead.first_contact_date),
                    _iso(lead.followup_1_date),
                    _iso(lead.followup_2_date),
                    _iso(lead.last_contact_date),
                    lead.next_action,
                    lead.notes,
                ),
            ).lastrowid
        return lead_id

    def get_lead(self, lead_id: int) -> StoredLead:
        row = self.conn.execute(_SELECT_LEADS + " WHERE l.id = ?", (lead_id,)).fetchone()
        if row is None:
            raise LeadNotFoundError(f"No lead with id {lead_id}")
        return _row_to_stored(row)

    def list_leads(self, status: LeadStatus | None = None) -> list[StoredLead]:
        if status is None:
            rows = self.conn.execute(_SELECT_LEADS + " ORDER BY l.id").fetchall()
        else:
            rows = self.conn.execute(_SELECT_LEADS + " WHERE l.status = ? ORDER BY l.id", (status.value,)).fetchall()
        return [_row_to_stored(r) for r in rows]

    def update_lead(self, lead_id: int, changes: Mapping[str, object]) -> StoredLead:
        """Change data fields on a lead. Status is changed with change_status, not here."""
        unknown = set(changes) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update field(s) {sorted(unknown)}, use change_status for lead_status")

        current = self.get_lead(lead_id)
        changes = dict(changes)
        if (
            "contact_email" in changes
            and "contact_email_verified" not in changes
            and changes["contact_email"] != current.lead.contact_email
        ):
            changes["contact_email_verified"] = False  # a changed address must be verified again
        merged = replace(current.lead, **changes)

        problems = validate_lead(merged) + _status_problems(merged, merged.lead_status)
        if problems:
            raise LeadValidationError(problems)

        if {"organization_number", "website", "company_name"} & changes.keys():
            others = [s for s in self.list_leads() if s.lead_id != lead_id]
            match = find_duplicate(merged, [s.lead for s in others])
            if match:
                existing_id = next(s.lead_id for s in others if s.lead is match.existing)
                raise DuplicateLeadError(match.matched_on, existing_id)

        with self.conn:
            self.conn.execute(
                "UPDATE company SET name = ?, organization_number = ?, website = ?, domain = ?, industry = ?,"
                " employee_count = ?, location = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    merged.company_name.strip(),
                    normalize_org_number(merged.organization_number),
                    merged.website,
                    normalize_domain(merged.website),
                    merged.industry,
                    merged.employee_count,
                    merged.location,
                    current.company_id,
                ),
            )
            self._write_contact(lead_id, current.company_id, merged)
            self.conn.execute(
                "UPDATE lead SET score = ?, source = ?, reason_for_fit = ?, identified_problem = ?,"
                " recommended_service = ?, first_contact_date = ?, followup_1_date = ?, followup_2_date = ?,"
                " last_contact_date = ?, next_action = ?, notes = ?, updated_at = CURRENT_TIMESTAMP"
                " WHERE id = ?",
                (
                    merged.lead_score,
                    merged.source,
                    merged.reason_for_fit,
                    merged.identified_problem,
                    merged.recommended_service,
                    _iso(merged.first_contact_date),
                    _iso(merged.followup_1_date),
                    _iso(merged.followup_2_date),
                    _iso(merged.last_contact_date),
                    merged.next_action,
                    merged.notes,
                    lead_id,
                ),
            )
        return self.get_lead(lead_id)

    def change_status(self, lead_id: int, target: LeadStatus) -> StoredLead:
        """Move a lead to a new status. Only allowed transitions, and the data must be in place."""
        current = self.get_lead(lead_id)
        transition(current.lead.lead_status, target)
        problems = _status_problems(current.lead, target)
        if problems:
            raise LeadValidationError(problems)
        with self.conn:
            self.conn.execute(
                "UPDATE lead SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (target.value, lead_id)
            )
        return self.get_lead(lead_id)

    def delete_lead(self, lead_id: int, confirmed_by: str, now: datetime | None = None) -> DeletionResult:
        """Delete a lead and its interactions, emails and tasks. A control point: needs a named human.

        The company and its contacts and sources go too, unless the company has other leads.
        DO_NOT_CONTACT leads are never deleted. They are the record that stops us from
        contacting the company again.
        """
        approver = require_human_approver(confirmed_by)
        current = self.get_lead(lead_id)
        if current.lead.lead_status == LeadStatus.DO_NOT_CONTACT:
            raise ValueError("DO_NOT_CONTACT leads cannot be deleted, they prevent renewed contact")

        with self.conn:
            self.conn.execute("DELETE FROM lead WHERE id = ?", (lead_id,))
            remaining = self.conn.execute(
                "SELECT COUNT(*) FROM lead WHERE company_id = ?", (current.company_id,)
            ).fetchone()[0]
            company_deleted = remaining == 0
            if company_deleted:
                self.conn.execute("DELETE FROM company WHERE id = ?", (current.company_id,))
        return DeletionResult(lead_id, company_deleted, approver, now or datetime.now())

    # -- interactions, emails, tasks, sources ------------------------------

    def add_interaction(
        self, lead_id: int, kind: InteractionKind, summary: str | None = None, occurred_at: datetime | None = None
    ) -> int:
        """Log an interaction. Anything except a note also moves last_contact_date forward."""
        current = self.get_lead(lead_id)
        when = occurred_at or datetime.now()
        with self.conn:
            interaction_id = self.conn.execute(
                "INSERT INTO interaction (lead_id, kind, occurred_at, summary) VALUES (?, ?, ?, ?)",
                (lead_id, kind.value, when.isoformat(), summary),
            ).lastrowid
            last = current.lead.last_contact_date
            if kind not in _NO_CONTACT_KINDS and (last is None or when.date() > last):
                self.conn.execute(
                    "UPDATE lead SET last_contact_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (when.date().isoformat(), lead_id),
                )
        return interaction_id

    def list_interactions(self, lead_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM interaction WHERE lead_id = ? ORDER BY occurred_at, id", (lead_id,)
        ).fetchall()

    def save_email(self, record: EmailRecord) -> int:
        self.get_lead(record.lead_id)
        with self.conn:
            return self.conn.execute(
                "INSERT INTO email (lead_id, kind, recipient, status, template_version, message_id,"
                " approved_by, approved_at, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.lead_id,
                    record.kind.value,
                    record.recipient,
                    record.status.value,
                    record.template_version,
                    record.message_id,
                    record.approved_by,
                    _iso(record.approved_at),
                    _iso(record.sent_at),
                ),
            ).lastrowid

    def update_email(self, email_id: int, record: EmailRecord) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE email SET status = ?, message_id = ?, approved_by = ?, approved_at = ?, sent_at = ?"
                " WHERE id = ?",
                (
                    record.status.value,
                    record.message_id,
                    record.approved_by,
                    _iso(record.approved_at),
                    _iso(record.sent_at),
                    email_id,
                ),
            )
        if cur.rowcount == 0:
            raise LookupError(f"No email with id {email_id}")

    def list_emails(self, lead_id: int) -> list[tuple[int, EmailRecord]]:
        rows = self.conn.execute("SELECT * FROM email WHERE lead_id = ? ORDER BY id", (lead_id,)).fetchall()
        return [
            (
                r["id"],
                EmailRecord(
                    lead_id=r["lead_id"],
                    recipient=r["recipient"],
                    kind=EmailKind(r["kind"]),
                    template_version=r["template_version"],
                    status=EmailStatus(r["status"]),
                    approved_by=r["approved_by"],
                    approved_at=_to_datetime(r["approved_at"]),
                    sent_at=_to_datetime(r["sent_at"]),
                    message_id=r["message_id"],
                ),
            )
            for r in rows
        ]

    def add_task(self, lead_id: int, description: str, due_date: date | None = None) -> int:
        if not description or not description.strip():
            raise LeadValidationError(["task description is required"])
        self.get_lead(lead_id)
        with self.conn:
            return self.conn.execute(
                "INSERT INTO task (lead_id, description, due_date) VALUES (?, ?, ?)",
                (lead_id, description.strip(), _iso(due_date)),
            ).lastrowid

    def complete_task(self, task_id: int) -> None:
        with self.conn:
            cur = self.conn.execute("UPDATE task SET done = 1 WHERE id = ?", (task_id,))
        if cur.rowcount == 0:
            raise LookupError(f"No task with id {task_id}")

    def list_open_tasks(self, due_on_or_before: date | None = None) -> list[sqlite3.Row]:
        if due_on_or_before is None:
            return self.conn.execute("SELECT * FROM task WHERE done = 0 ORDER BY due_date, id").fetchall()
        return self.conn.execute(
            "SELECT * FROM task WHERE done = 0 AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date, id",
            (due_on_or_before.isoformat(),),
        ).fetchall()

    def add_research_source(self, lead_id: int, source: ResearchSource) -> int:
        if not source.url or not source.url.strip():
            raise LeadValidationError(["research source url is required"])
        current = self.get_lead(lead_id)
        with self.conn:
            return self.conn.execute(
                "INSERT INTO research_source (company_id, url, source_type, retrieved_at, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    current.company_id,
                    source.url.strip(),
                    source.source_type,
                    (source.retrieved_at or datetime.now()).isoformat(),
                    source.note,
                ),
            ).lastrowid

    def list_research_sources(self, lead_id: int) -> list[ResearchSource]:
        current = self.get_lead(lead_id)
        rows = self.conn.execute(
            "SELECT * FROM research_source WHERE company_id = ? ORDER BY id", (current.company_id,)
        ).fetchall()
        return [
            ResearchSource(r["url"], r["source_type"], _to_datetime(r["retrieved_at"]), r["note"]) for r in rows
        ]

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _has_contact_data(lead: Lead) -> bool:
        return bool(lead.contact_name or lead.contact_role or lead.contact_email)

    def _insert_contact(self, company_id: int, lead: Lead) -> int | None:
        if not self._has_contact_data(lead):
            return None
        return self.conn.execute(
            "INSERT INTO contact (company_id, name, role, email, email_verified, source) VALUES (?, ?, ?, ?, ?, ?)",
            (
                company_id,
                lead.contact_name,
                lead.contact_role,
                lead.contact_email,
                int(lead.contact_email_verified),
                lead.source,
            ),
        ).lastrowid

    def _write_contact(self, lead_id: int, company_id: int, merged: Lead) -> None:
        contact_id = self.conn.execute("SELECT contact_id FROM lead WHERE id = ?", (lead_id,)).fetchone()[0]
        if contact_id is None:
            new_id = self._insert_contact(company_id, merged)
            if new_id is not None:
                self.conn.execute("UPDATE lead SET contact_id = ? WHERE id = ?", (new_id, lead_id))
            return
        self.conn.execute(
            "UPDATE contact SET name = ?, role = ?, email = ?, email_verified = ? WHERE id = ?",
            (
                merged.contact_name,
                merged.contact_role,
                merged.contact_email,
                int(merged.contact_email_verified),
                contact_id,
            ),
        )
