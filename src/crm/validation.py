"""Lead data validation. Plain code, no LLM."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from src.crm.models import Lead

_ORG_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def normalize_org_number(raw: str | None) -> str | None:
    """Strip spaces, an NO prefix and an MVA suffix. Return 9 digits or None."""
    if not raw:
        return None
    text = raw.strip().upper().replace(" ", "").replace(".", "")
    if text.startswith("NO"):
        text = text[2:]
    if text.endswith("MVA"):
        text = text[:-3]
    return text if re.fullmatch(r"\d{9}", text) else None


def is_valid_org_number(raw: str | None) -> bool:
    """Norwegian organisation number: 9 digits with a modulus 11 check digit."""
    digits = normalize_org_number(raw)
    if digits is None:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _ORG_WEIGHTS))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[8])


def is_valid_email_format(raw: str | None) -> bool:
    return bool(raw) and _EMAIL_RE.match(raw.strip()) is not None


def normalize_domain(url_or_domain: str | None) -> str | None:
    """Return a bare lowercase domain without scheme, www, port or path. None if unusable."""
    if not url_or_domain:
        return None
    text = url_or_domain.strip().lower()
    if "//" not in text:
        text = "//" + text
    host = urlparse(text).hostname
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host if _DOMAIN_RE.match(host) else None


def validate_lead(lead: Lead) -> list[str]:
    """Return a list of problems. Empty list means the lead is valid."""
    problems: list[str] = []

    if not lead.company_name or not lead.company_name.strip():
        problems.append("company_name is required")
    if lead.organization_number and not is_valid_org_number(lead.organization_number):
        problems.append("organization_number is not a valid 9 digit number")
    if lead.website and normalize_domain(lead.website) is None:
        problems.append("website is not a valid domain or URL")
    if lead.contact_email and not is_valid_email_format(lead.contact_email):
        problems.append("contact_email has invalid format")
    if lead.contact_email_verified and not lead.contact_email:
        problems.append("contact_email_verified is set but contact_email is missing")
    if lead.lead_score is not None and not 0 <= lead.lead_score <= 100:
        problems.append("lead_score must be between 0 and 100")
    if lead.employee_count is not None and lead.employee_count < 0:
        problems.append("employee_count cannot be negative")

    first, f1, f2 = lead.first_contact_date, lead.followup_1_date, lead.followup_2_date
    if first and f1 and f1 < first:
        problems.append("followup_1_date is before first_contact_date")
    if f1 and f2 and f2 < f1:
        problems.append("followup_2_date is before followup_1_date")
    if f2 and not f1:
        problems.append("followup_2_date is set without followup_1_date")

    return problems
