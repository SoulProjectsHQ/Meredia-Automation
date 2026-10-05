"""Research of one company: registry lookup, website analysis and the sources behind them.

research_company puts the registry (src.integrations.brreg) and the website analysis
(src.research.website) together into one ResearchResult. It does plain orchestration. It judges
nothing: whether the company fits, what its problem is and what Meredia should offer are later
qualification steps, so result_to_lead leaves those fields empty.

Nothing in this module sends email or writes to the CRM on its own. The only write is
store_research, which saves research sources through LeadRepository.

UNVERIFIED: the registry fields used here (RegistryCompany) come from a Brreg response shape that
was written from memory, because data.brreg.no was not reachable when src/integrations/brreg.py
was made. Treat every registry value as unconfirmed until scripts/check_brreg.py has been run
against the live API.

Warnings are short codes, optionally followed by ": detail". They never contain error messages or
response bodies. A result with warnings is still returned, so a partial result is not lost:

    registry_lookup_failed: <error type>   retryable error that survived the retries
    registry_not_found                     the number is not in the register
    registry_source_url_missing            the registry gave no URL, so no source could be recorded
    inactive_company: <reason>             bankrupt, under_liquidation or deleted, website not fetched
    website_invalid                        the website is not a usable domain or URL, nothing fetched
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from src.automation.errors import AutomationError, ErrorType
from src.automation.retry import RateLimiter, retry_call
from src.crm.models import Lead, LeadStatus, ResearchSource
from src.crm.repository import LeadRepository, LeadValidationError
from src.crm.validation import is_valid_org_number, normalize_domain, normalize_org_number
from src.integrations.brreg import CompanyRegistry, RegistryCompany
from src.integrations.http import HttpClient
from src.research.website import Observation, WebsiteAnalysis, fetch_and_analyze

SOURCE_TYPE_REGISTRY = "brreg"
SOURCE_TYPE_WEBSITE = "website"

WARNING_REGISTRY_LOOKUP_FAILED = "registry_lookup_failed"
WARNING_REGISTRY_NOT_FOUND = "registry_not_found"
WARNING_REGISTRY_SOURCE_URL_MISSING = "registry_source_url_missing"
WARNING_INACTIVE_COMPANY = "inactive_company"
WARNING_WEBSITE_INVALID = "website_invalid"

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ResearchResult:
    """What one research run found. None means not looked up or not found, never a guess."""

    organization_number: str | None = None  # normalized to 9 digits, None when not given
    registry: RegistryCompany | None = None
    website: WebsiteAnalysis | None = None
    sources: list[ResearchSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when nothing went wrong. It does not mean that every field is known."""
        return not self.warnings


# ---------------------------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------------------------


def _blank_to_none(value: object, name: str) -> str | None:
    """A trimmed string, or None for None and blank text. Anything else is a validation error."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AutomationError(ErrorType.VALIDATION_ERROR, f"{name} must be a string")
    return value.strip() or None


def _clean_org_number(raw: object) -> str | None:
    """The 9 digit organisation number, or None when not given. An invalid number is an error."""
    text = _blank_to_none(raw, "organization_number")
    if text is None:
        return None
    if not is_valid_org_number(text):
        raise AutomationError(ErrorType.VALIDATION_ERROR, "Invalid organisation number")
    return normalize_org_number(text)


def _warning(code: str, detail: str | None = None) -> str:
    return code if detail is None else f"{code}: {detail}"


# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------


def _inactive_reason(company: RegistryCompany) -> str | None:
    """Reason codes joined with a comma, or None for an active company."""
    reasons = []
    if company.bankrupt:
        reasons.append("bankrupt")
    if company.under_liquidation:
        reasons.append("under_liquidation")
    if company.deleted_date is not None:
        reasons.append("deleted")
    return ", ".join(reasons) or None


def _lookup_company(
    registry: CompanyRegistry,
    number: str,
    rate_limiter: RateLimiter | None,
    sleep: Callable[[float], None],
    warnings: list[str],
) -> RegistryCompany | None:
    """Look the company up with retries. A retryable error that survives them becomes a warning."""

    def attempt() -> RegistryCompany | None:
        if rate_limiter is not None:
            rate_limiter.wait()
        return registry.lookup(number)

    try:
        company = retry_call(attempt, sleep=sleep)
    except AutomationError as error:
        if not error.retryable:
            raise
        warnings.append(_warning(WARNING_REGISTRY_LOOKUP_FAILED, str(error.error_type)))
        return None
    if company is None:
        warnings.append(WARNING_REGISTRY_NOT_FOUND)
    return company


def _form_text(company: RegistryCompany) -> str:
    code, description = company.organization_form, company.organization_form_description
    if code and description:
        return f"{code} ({description})"
    return code or description or UNKNOWN


def _registry_source(company: RegistryCompany, retrieved_at: datetime) -> ResearchSource:
    employees = UNKNOWN if company.employee_count is None else str(company.employee_count)
    reason = _inactive_reason(company)
    status = "aktiv" if reason is None else f"inaktiv ({reason})"
    note = f"Antall ansatte: {employees}. Organisasjonsform: {_form_text(company)}. Status: {status}."
    return ResearchSource(
        url=company.source_url.strip(), source_type=SOURCE_TYPE_REGISTRY, retrieved_at=retrieved_at, note=note
    )


# ---------------------------------------------------------------------------------------------
# Website
# ---------------------------------------------------------------------------------------------


def _website_source(analysis: WebsiteAnalysis, retrieved_at: datetime) -> ResearchSource:
    """Source for the page. An unreachable site has no final_url, so the requested URL is used."""
    note = " ".join(observation.text for observation in analysis.observations) or None
    return ResearchSource(
        url=analysis.final_url or analysis.url,
        source_type=SOURCE_TYPE_WEBSITE,
        retrieved_at=retrieved_at,
        note=note,
    )


# ---------------------------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------------------------


def research_company(
    *,
    registry: CompanyRegistry,
    http: HttpClient,
    organization_number: str | None = None,
    website: str | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
    today: date | None = None,
) -> ResearchResult:
    """Research one company from its organisation number, its website or both.

    With a number the registry is asked through retry_call, and rate_limiter.wait() runs before
    every attempt. A retryable error that survives the retries becomes a warning and the run goes
    on without registry data. A non-retryable error propagates. A number that is not in the
    register gives the warning registry_not_found.

    The website to analyse is the given one, else the one in the register. An inactive company
    (bankrupt, under liquidation or deleted) is not fetched at all, it gets the warning
    inactive_company. Otherwise fetch_and_analyze runs after rate_limiter.wait(). A website that is
    not a usable address becomes a warning, any other error propagates. A website that cannot be
    reached is not an error: the analysis then says so in its observations.

    now stamps retrieved_at on the sources (default datetime.now()) and today is handed to the
    website analysis for its year check. Raises AutomationError VALIDATION_ERROR when neither
    organization_number nor website is given, or when the number is not a valid organisation
    number. Sends nothing and writes nothing.
    """
    number = _clean_org_number(organization_number)
    site = _blank_to_none(website, "website")
    if number is None and site is None:
        raise AutomationError(ErrorType.VALIDATION_ERROR, "organization_number or website is required")

    retrieved_at = now if now is not None else datetime.now()
    warnings: list[str] = []
    sources: list[ResearchSource] = []

    company: RegistryCompany | None = None
    if number is not None:
        company = _lookup_company(registry, number, rate_limiter, sleep, warnings)
    if company is not None:
        if (company.source_url or "").strip():
            sources.append(_registry_source(company, retrieved_at))
        else:
            warnings.append(WARNING_REGISTRY_SOURCE_URL_MISSING)

    target = site
    if target is None and company is not None:
        target = _blank_to_none(company.website, "registry website")
    reason = _inactive_reason(company) if company is not None else None

    analysis: WebsiteAnalysis | None = None
    if reason is not None:
        warnings.append(_warning(WARNING_INACTIVE_COMPANY, reason))
    elif target is not None:
        if rate_limiter is not None:
            rate_limiter.wait()
        try:
            analysis = fetch_and_analyze(http, target, today=today)
        except AutomationError as error:
            if error.error_type is not ErrorType.VALIDATION_ERROR:
                raise
            warnings.append(WARNING_WEBSITE_INVALID)
        else:
            sources.append(_website_source(analysis, retrieved_at))

    return ResearchResult(
        organization_number=number, registry=company, website=analysis, sources=sources, warnings=warnings
    )


def inactive_reason(result: ResearchResult) -> str | None:
    """Why the company is inactive (bankrupt, under_liquidation, deleted), or None.

    None also when there is no registry data, because nothing is known then.
    """
    if result.registry is None:
        return None
    return _inactive_reason(result.registry)


def improvement_signals(result: ResearchResult) -> list[Observation]:
    """The website observations that may point to an area where Meredia can contribute.

    A signal is an input for a human and for scoring, not a verdict. Empty when no website was
    analysed.
    """
    if result.website is None:
        return []
    return [observation for observation in result.website.observations if observation.improvement_signal]


def _first_text(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def result_to_lead(result: ResearchResult, *, source: str, company_name: str | None = None) -> Lead:
    """Build a NEW lead from a research result. Only registry facts are used.

    The name comes from the register, else from company_name, otherwise ValueError. The website
    is the registered one as written (raw), else the one that was analysed. A registered website
    that is not a usable address is left out, so one bad register entry cannot make the lead
    invalid. Unknown values stay None. The lead has no contact fields: an e-mail address found on
    the website is never copied into contact_email and never marked as verified. reason_for_fit
    and identified_problem stay None, because judging them is a later qualification step. The
    caller should check inactive_reason first, an inactive company is normally not a lead.
    """
    registry = result.registry
    name = _first_text(registry.name if registry is not None else None, company_name)
    if name is None:
        raise ValueError("No company name found in the research result, pass company_name")

    website = None
    if registry is not None and registry.website and normalize_domain(registry.website) is not None:
        website = registry.website
    if website is None and result.website is not None:
        website = result.website.url

    return Lead(
        company_name=name,
        organization_number=registry.organization_number if registry is not None else result.organization_number,
        website=website,
        industry=registry.industry_description if registry is not None else None,
        employee_count=registry.employee_count if registry is not None else None,
        location=_first_text(registry.post_place, registry.municipality) if registry is not None else None,
        source=source,
        lead_status=LeadStatus.NEW,
    )


def store_research(repo: LeadRepository, lead_id: int, result: ResearchResult) -> list[int]:
    """Save every source of the result on the lead and return the new source ids, in order.

    A source without a URL is refused before anything is written, so a bad result leaves no
    partial rows. Calling this twice stores the sources twice. Writes only through the repository.
    """
    if any(not source.url or not source.url.strip() for source in result.sources):
        raise LeadValidationError(["research source url is required"])
    return [repo.add_research_source(lead_id, source) for source in result.sources]
