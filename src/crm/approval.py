"""Shared check for control points. A named human must confirm, never the automation itself."""

from __future__ import annotations

# Identities that are not a human approver.
NON_HUMAN_APPROVERS = frozenset({"ai", "llm", "claude", "system", "automation", "bot"})


def require_human_approver(name: str | None) -> str:
    """Return the cleaned name, or raise ValueError if it is empty or a non-human identity."""
    approver = (name or "").strip()
    if not approver or approver.lower() in NON_HUMAN_APPROVERS:
        raise ValueError("Approval requires a named human approver")
    return approver
