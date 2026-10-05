"""Duplicate detection. Organisation number first, then domain, then company name."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from src.crm.models import Lead
from src.crm.validation import normalize_domain, normalize_org_number

_LEGAL_SUFFIXES = frozenset({"as", "asa", "ans", "da", "enk", "sa", "ba", "nuf", "ab", "aps", "ltd"})


@dataclass(frozen=True)
class DuplicateMatch:
    matched_on: str  # organization_number | domain | company_name
    existing: Lead


def normalize_company_name(name: str | None) -> str | None:
    """Casefold, drop punctuation and trailing legal suffixes (AS, ANS, ...)."""
    if not name:
        return None
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"\ba/s\b", "as", text)
    tokens = re.sub(r"[^\w\s]", " ", text).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or None


def find_duplicate(candidate: Lead, existing: Iterable[Lead]) -> DuplicateMatch | None:
    """Return the first match, checking the strongest identifier across all leads first."""
    existing = list(existing)
    org = normalize_org_number(candidate.organization_number)
    domain = normalize_domain(candidate.website)
    name = normalize_company_name(candidate.company_name)

    checks = (
        ("organization_number", org, lambda lead: normalize_org_number(lead.organization_number)),
        ("domain", domain, lambda lead: normalize_domain(lead.website)),
        ("company_name", name, lambda lead: normalize_company_name(lead.company_name)),
    )
    for field, value, extract in checks:
        if value is None:
            continue
        for lead in existing:
            if extract(lead) == value:
                return DuplicateMatch(matched_on=field, existing=lead)
    return None
