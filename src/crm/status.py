"""Allowed lead status transitions. Plain code, no LLM.

Forward moves follow the sales flow. Every open status can also go to LOST or
DO_NOT_CONTACT. WON and LOST can only be followed by DO_NOT_CONTACT. DO_NOT_CONTACT is final.
"""

from __future__ import annotations

from src.crm.models import LeadStatus

S = LeadStatus

_FORWARD: dict[LeadStatus, frozenset[LeadStatus]] = {
    S.NEW: frozenset({S.RESEARCHED}),
    S.RESEARCHED: frozenset({S.QUALIFIED}),
    S.QUALIFIED: frozenset({S.READY_TO_CONTACT}),
    S.READY_TO_CONTACT: frozenset({S.CONTACTED}),
    S.CONTACTED: frozenset({S.FOLLOWUP_1, S.REPLIED}),
    S.FOLLOWUP_1: frozenset({S.FOLLOWUP_2, S.REPLIED}),
    S.FOLLOWUP_2: frozenset({S.REPLIED}),
    S.REPLIED: frozenset({S.INTERESTED, S.MEETING}),
    S.INTERESTED: frozenset({S.MEETING, S.PROPOSAL}),
    S.MEETING: frozenset({S.PROPOSAL, S.WON}),
    S.PROPOSAL: frozenset({S.WON}),
}

_OPEN_EXITS = frozenset({S.LOST, S.DO_NOT_CONTACT})

ALLOWED_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    **{status: forward | _OPEN_EXITS for status, forward in _FORWARD.items()},
    S.WON: frozenset({S.DO_NOT_CONTACT}),
    S.LOST: frozenset({S.DO_NOT_CONTACT}),
    S.DO_NOT_CONTACT: frozenset(),
}


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(current: LeadStatus, target: LeadStatus) -> LeadStatus:
    """Return target if the move is allowed, otherwise raise ValueError."""
    if not can_transition(current, target):
        raise ValueError(f"Status change not allowed: {current} -> {target}")
    return target


def is_contactable(status: LeadStatus) -> bool:
    """True if a new cold mail may be prepared for a lead in this status."""
    return status == S.READY_TO_CONTACT
