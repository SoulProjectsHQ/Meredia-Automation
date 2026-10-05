"""Email status handling. DRAFT, APPROVED, SENT.

An AI generated mail is never approved automatically. Approval needs a named human.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class EmailStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SENT = "SENT"


class EmailKind(StrEnum):
    COLD = "cold"
    FOLLOWUP_1 = "followup_1"
    FOLLOWUP_2 = "followup_2"


# Identities that are not a human approver.
NON_HUMAN_APPROVERS = frozenset({"ai", "llm", "claude", "system", "automation", "bot"})


@dataclass(frozen=True)
class EmailRecord:
    """Log entry for one mail. Holds no body, only what the log needs."""

    lead_id: int
    recipient: str
    kind: EmailKind
    template_version: str
    status: EmailStatus = EmailStatus.DRAFT
    approved_by: str | None = None
    approved_at: datetime | None = None
    sent_at: datetime | None = None
    message_id: str | None = None


def approve(record: EmailRecord, approved_by: str, now: datetime) -> EmailRecord:
    if record.status != EmailStatus.DRAFT:
        raise ValueError(f"Only a DRAFT can be approved, status is {record.status}")
    approver = (approved_by or "").strip()
    if not approver or approver.lower() in NON_HUMAN_APPROVERS:
        raise ValueError("Approval requires a named human approver")
    return replace(record, status=EmailStatus.APPROVED, approved_by=approver, approved_at=now)


def mark_sent(record: EmailRecord, now: datetime, message_id: str | None = None) -> EmailRecord:
    if record.status != EmailStatus.APPROVED:
        raise ValueError(f"Only an APPROVED mail can be sent, status is {record.status}")
    return replace(record, status=EmailStatus.SENT, sent_at=now, message_id=message_id)


def revert_to_draft(record: EmailRecord) -> EmailRecord:
    """Editing an approved mail voids the approval."""
    if record.status == EmailStatus.SENT:
        raise ValueError("A SENT mail cannot be changed")
    return replace(record, status=EmailStatus.DRAFT, approved_by=None, approved_at=None)
