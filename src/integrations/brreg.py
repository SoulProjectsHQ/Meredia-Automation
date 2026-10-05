"""Brreg (Enhetsregisteret) lookup by organisation number.

Public company facts only. Nothing about board members, roles or other persons is read, stored
or returned, and the raw response body is never kept or logged. The module reads a fixed list of
keys and ignores everything else.

UNVERIFIED: data.brreg.no was not reachable when this was written, so the response shape below
comes from memory and has not been checked against the live API. Run scripts/check_brreg.py
against a real organisation number once network access is allowed, and compare the output with
the register before relying on any field. Fields the API does not return come out as None
(unknown), never as a guess.

Expected JSON from GET <base_url>/enheter/<organisation number>:

    organisasjonsnummer                          "912345678"
    navn                                         "Eksempel AS"
    organisasjonsform                            {"kode": "AS", "beskrivelse": "Aksjeselskap"}
    registreringsdatoEnhetsregisteret            "2015-03-12" (YYYY-MM-DD)
    naeringskode1                                {"kode": "62.010", "beskrivelse": "..."}
    antallAnsatte                                12 (int, absent when not registered)
    hjemmeside                                   "www.eksempel.no" (no scheme, may be absent)
    forretningsadresse                           {"kommune": "...", "poststed": "...", ...}
    konkurs                                      bool
    underAvvikling                               bool
    underTvangsavviklingEllerTvangsopplosning    bool, treated as under_liquidation too
    slettedato                                   "2021-06-30", only on deleted units

HTTP 404 means the number is not in the register. HTTP 410 means the unit is deleted and comes
with a partial body that holds slettedato.

This module does no retries. The caller wraps lookup with src.automation.retry.retry_call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from src.automation.errors import AutomationError, ErrorType
from src.crm.validation import is_valid_org_number, normalize_org_number
from src.integrations.http import HttpClient

DEFAULT_BASE_URL = "https://data.brreg.no/enhetsregisteret/api"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:T.*)?")


@dataclass(frozen=True)
class RegistryCompany:
    """Company facts from the register. None means unknown, never an empty guess."""

    organization_number: str
    name: str | None
    organization_form: str | None = None  # code, for example AS or ENK
    organization_form_description: str | None = None
    industry_code: str | None = None
    industry_description: str | None = None
    employee_count: int | None = None  # None means unknown, 0 means registered as zero
    website: str | None = None  # as registered, not normalized
    municipality: str | None = None
    post_place: str | None = None
    registered_date: date | None = None
    bankrupt: bool = False
    under_liquidation: bool = False
    deleted_date: date | None = None
    source_url: str = ""  # the API URL used, empty when the company did not come from an API

    @property
    def is_active(self) -> bool:
        """True only when not bankrupt, not under liquidation and not deleted."""
        return not self.bankrupt and not self.under_liquidation and self.deleted_date is None


class CompanyRegistry(Protocol):
    def lookup(self, organization_number: str) -> RegistryCompany | None:
        """Return the company, or None when the number is not in the register."""
        ...


def _service_error(message: str) -> AutomationError:
    return AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, message)


def _text(value: Any) -> str | None:
    """A non-empty string with surrounding whitespace trimmed, otherwise None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _count(value: Any) -> int | None:
    """A non-negative int. bool is an int in Python but never a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    match = _DATE_RE.fullmatch(value.strip())
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _flag(data: dict[str, Any], key: str) -> bool:
    """A status flag. Absent or null is False. Any other non-bool is an error.

    A flag with an unexpected type is not guessed to be False, because that could report a
    bankrupt company as active.
    """
    value = data.get(key)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise _service_error(f"Brreg field {key} has an unexpected type")


def _parse_object(body: Any) -> dict[str, Any]:
    """Parse a JSON object. The body is never echoed in an error."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError, RecursionError):
        raise _service_error("Brreg returned a body that is not valid JSON") from None
    if not isinstance(data, dict):
        raise _service_error("Brreg returned JSON that is not an object")
    return data


def _build_company(
    data: dict[str, Any], organization_number: str, source_url: str, *, deleted: bool
) -> RegistryCompany:
    returned = data.get("organisasjonsnummer")
    # Only a string can be compared. An absent or non-string value falls back to the number asked
    # for, which is also the one in the URL.
    if isinstance(returned, str) and normalize_org_number(returned) != organization_number:
        raise _service_error(
            f"Brreg returned a different organisation number than requested ({organization_number})"
        )

    deleted_date = _date(data.get("slettedato"))
    if deleted and deleted_date is None:
        # RegistryCompany cannot say "deleted, date unknown", and is_active would turn True.
        raise _service_error(f"Brreg reported {organization_number} as deleted without a usable slettedato")

    form = _section(data, "organisasjonsform")
    industry = _section(data, "naeringskode1")
    address = _section(data, "forretningsadresse")
    return RegistryCompany(
        organization_number=organization_number,
        name=_text(data.get("navn")),
        organization_form=_text(form.get("kode")),
        organization_form_description=_text(form.get("beskrivelse")),
        industry_code=_text(industry.get("kode")),
        industry_description=_text(industry.get("beskrivelse")),
        employee_count=_count(data.get("antallAnsatte")),
        website=_text(data.get("hjemmeside")),
        municipality=_text(address.get("kommune")),
        post_place=_text(address.get("poststed")),
        registered_date=_date(data.get("registreringsdatoEnhetsregisteret")),
        bankrupt=_flag(data, "konkurs"),
        under_liquidation=(
            _flag(data, "underAvvikling") or _flag(data, "underTvangsavviklingEllerTvangsopplosning")
        ),
        deleted_date=deleted_date,
        source_url=source_url,
    )


class BrregClient:
    """CompanyRegistry on top of the Brreg open API. Takes an HttpClient so tests stay offline."""

    def __init__(self, http: HttpClient, base_url: str = DEFAULT_BASE_URL) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    def lookup(self, organization_number: str) -> RegistryCompany | None:
        """Look up one company.

        Raises AutomationError VALIDATION_ERROR for an invalid number (no request is made) and
        EXTERNAL_SERVICE_ERROR for a response that cannot be trusted. Errors from the HttpClient
        pass through unchanged.
        """
        number = self._validated_number(organization_number)
        url = f"{self._base_url}/enheter/{number}"
        response = self._http.get(url)

        if response.status == 404:
            return None
        if response.status == 410:
            return _build_company(_parse_object(response.body), number, url, deleted=True)
        if 200 <= response.status < 300:
            return _build_company(_parse_object(response.body), number, url, deleted=False)
        raise _service_error(f"Brreg returned unexpected status {response.status}")

    @staticmethod
    def _validated_number(organization_number: str) -> str:
        number = None
        if isinstance(organization_number, str) and is_valid_org_number(organization_number):
            number = normalize_org_number(organization_number)
        if number is None:
            raise AutomationError(ErrorType.VALIDATION_ERROR, "Invalid organisation number")
        return number
