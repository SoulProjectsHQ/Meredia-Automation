"""CRM domain types. Lead statuses are a fixed set, do not add new ones without a good reason."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "NEW"
    RESEARCHED = "RESEARCHED"
    QUALIFIED = "QUALIFIED"
    READY_TO_CONTACT = "READY_TO_CONTACT"
    CONTACTED = "CONTACTED"
    FOLLOWUP_1 = "FOLLOWUP_1"
    FOLLOWUP_2 = "FOLLOWUP_2"
    REPLIED = "REPLIED"
    INTERESTED = "INTERESTED"
    MEETING = "MEETING"
    PROPOSAL = "PROPOSAL"
    WON = "WON"
    LOST = "LOST"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"


@dataclass
class Lead:
    """Flat CRM view of a lead. Missing or unconfirmed values are None, shown as UNKNOWN in the UI."""

    company_name: str
    organization_number: str | None = None
    website: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    location: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    contact_email: str | None = None
    contact_email_verified: bool = False
    source: str | None = None
    lead_score: int | None = None
    lead_status: LeadStatus = LeadStatus.NEW
    reason_for_fit: str | None = None
    identified_problem: str | None = None
    recommended_service: str | None = None
    first_contact_date: date | None = None
    followup_1_date: date | None = None
    followup_2_date: date | None = None
    last_contact_date: date | None = None
    next_action: str | None = None
    notes: str | None = None
