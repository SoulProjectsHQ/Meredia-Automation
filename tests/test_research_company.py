import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from unittest import mock

from src.automation.errors import AutomationError, ErrorType
from src.automation.retry import RateLimiter
from src.crm.db import connect, init_db
from src.crm.models import LeadStatus, ResearchSource
from src.crm.repository import LeadNotFoundError, LeadRepository, LeadValidationError
from src.integrations.brreg import RegistryCompany
from src.integrations.http import HttpResponse
from src.research.company import (
    ResearchResult,
    improvement_signals,
    inactive_reason,
    research_company,
    result_to_lead,
    store_research,
)
from src.research.website import Observation
from tests.test_validation import valid_org_number

NOW = datetime(2026, 10, 6, 9, 30)
TODAY = date(2026, 10, 6)
ORG = valid_org_number()
REGISTRY_URL = f"https://register.example.no/enheter/{ORG}"
HTTPS_URL = "https://eksempel.no/"
HTTP_URL = "http://eksempel.no/"

GOOD_PAGE = """<html lang="nb"><head><title>Eksempel Regnskap AS</title>
<meta name="description" content="Regnskap for små bedrifter">
<meta name="viewport" content="width=device-width"></head><body>
<h1>Regnskap</h1><a href="/kontakt">Kontakt oss</a>
<p><a href="mailto:post@eksempel.no">post@eksempel.no</a></p>
<footer>&copy; 2026 Eksempel Regnskap AS</footer></body></html>"""

# No title, no description, no viewport, no h1, no CTA and no contact link: only signals.
BARE_PAGE = "<html><body>Hei</body></html>"

OLD_PAGE = GOOD_PAGE.replace("2026", "2015")


def make_company(**overrides) -> RegistryCompany:
    data = dict(
        organization_number=ORG,
        name="Eksempel Regnskap AS",
        organization_form="AS",
        organization_form_description="Aksjeselskap",
        industry_code="69.201",
        industry_description="Regnskap",
        employee_count=12,
        website="www.eksempel.no",
        municipality="Trondheim",
        post_place="Heimdal",
        source_url=REGISTRY_URL,
    )
    data.update(overrides)
    return RegistryCompany(**data)


def page_response(body: str, url: str = HTTPS_URL) -> HttpResponse:
    return HttpResponse(url=url, status=200, body=body, elapsed_ms=300, size_bytes=len(body), content_type="text/html")


def unavailable() -> AutomationError:
    return AutomationError(ErrorType.TEMPORARY_ERROR, "no answer")


class FakeRegistry:
    """CompanyRegistry fake. Each lookup consumes the next outcome: a company, None or an exception."""

    def __init__(self, *outcomes, events: list[str] | None = None) -> None:
        self.outcomes = list(outcomes)
        self.events = events if events is not None else []
        self.calls: list[str] = []

    def lookup(self, organization_number: str) -> RegistryCompany | None:
        self.calls.append(organization_number)
        self.events.append("lookup")
        assert self.outcomes, "FakeRegistry has no outcome left"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeHttp:
    """HttpClient fake. URLs without an entry raise a temporary error, as an unreachable site would."""

    def __init__(self, pages: dict[str, HttpResponse] | None = None, events: list[str] | None = None) -> None:
        self.pages = pages or {}
        self.events = events if events is not None else []
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float = 10.0, max_bytes: int = 2_000_000) -> HttpResponse:
        self.calls.append(url)
        self.events.append("get")
        if url not in self.pages:
            raise unavailable()
        return self.pages[url]


class FakeLimiter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def wait(self) -> None:
        self.events.append("wait")


def site(body: str = GOOD_PAGE) -> FakeHttp:
    return FakeHttp({HTTPS_URL: page_response(body)})


def run(registry=None, http=None, **kwargs) -> ResearchResult:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("today", TODAY)
    return research_company(
        registry=registry if registry is not None else FakeRegistry(),
        http=http if http is not None else FakeHttp(),
        **kwargs,
    )


def new_repo() -> LeadRepository:
    conn = connect(":memory:")
    init_db(conn)
    return LeadRepository(conn)


class HappyPathTests(unittest.TestCase):
    def test_both_sources_are_built(self):
        registry = FakeRegistry(make_company())
        http = site()
        result = run(registry, http, organization_number=ORG)

        self.assertTrue(result.complete)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.organization_number, ORG)
        self.assertEqual(result.registry, make_company())
        self.assertTrue(result.website.reachable)
        self.assertEqual(registry.calls, [ORG])
        self.assertEqual(http.calls, [HTTPS_URL])

        registry_source, website_source = result.sources
        self.assertEqual(registry_source.url, REGISTRY_URL)
        self.assertEqual(registry_source.source_type, "brreg")
        self.assertEqual(registry_source.retrieved_at, NOW)
        self.assertIn("Antall ansatte: 12", registry_source.note)
        self.assertIn("AS (Aksjeselskap)", registry_source.note)
        self.assertIn("Status: aktiv", registry_source.note)
        self.assertEqual(website_source.url, HTTPS_URL)
        self.assertEqual(website_source.source_type, "website")
        self.assertEqual(website_source.retrieved_at, NOW)
        self.assertEqual(website_source.note, " ".join(o.text for o in result.website.observations))

    def test_website_note_never_repeats_the_email_address(self):
        result = run(FakeRegistry(make_company()), site(), organization_number=ORG)
        self.assertEqual(result.website.contact_emails, ["post@eksempel.no"])
        self.assertNotIn("post@eksempel.no", result.sources[1].note)

    def test_final_url_is_the_website_source_url(self):
        http = FakeHttp({HTTPS_URL: page_response(GOOD_PAGE, url="https://www.eksempel.no/hjem")})
        result = run(http=http, website="eksempel.no")
        self.assertEqual(result.sources[0].url, "https://www.eksempel.no/hjem")

    def test_organization_number_is_normalized(self):
        registry = FakeRegistry(make_company())
        result = run(registry, site(), organization_number=f" NO {ORG} MVA ")
        self.assertEqual(result.organization_number, ORG)
        self.assertEqual(registry.calls, [ORG])

    def test_now_defaults_to_the_current_time(self):
        result = research_company(
            registry=FakeRegistry(make_company()), http=site(), organization_number=ORG, today=TODAY
        )
        self.assertTrue(all(isinstance(s.retrieved_at, datetime) for s in result.sources))

    def test_today_reaches_the_website_analysis(self):
        http = FakeHttp({HTTPS_URL: page_response(OLD_PAGE)})
        old = run(http=http, website="eksempel.no", today=date(2026, 10, 6))
        self.assertIn("old_copyright_year", [o.code for o in old.website.observations])
        recent = run(http=http, website="eksempel.no", today=date(2016, 10, 6))
        self.assertNotIn("old_copyright_year", [o.code for o in recent.website.observations])

    def test_result_is_frozen(self):
        result = run(FakeRegistry(make_company()), site(), organization_number=ORG)
        with self.assertRaises(FrozenInstanceError):
            result.warnings = []


class InputTests(unittest.TestCase):
    def test_no_inputs_is_a_validation_error(self):
        registry, http = FakeRegistry(), FakeHttp()
        with self.assertRaises(AutomationError) as ctx:
            run(registry, http)
        self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)
        self.assertEqual(registry.calls, [])
        self.assertEqual(http.calls, [])

    def test_blank_inputs_count_as_missing(self):
        with self.assertRaises(AutomationError) as ctx:
            run(organization_number="  ", website="")
        self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)

    def test_invalid_organization_number_never_reaches_the_registry(self):
        registry = FakeRegistry(make_company())
        wrong = ORG[:8] + str((int(ORG[8]) + 1) % 10)
        for bad in (wrong, "123", "abc"):
            with self.subTest(bad=bad), self.assertRaises(AutomationError) as ctx:
                run(registry, organization_number=bad, website="eksempel.no")
            self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)
        self.assertEqual(registry.calls, [])

    def test_non_string_input_is_a_validation_error(self):
        with self.assertRaises(AutomationError) as ctx:
            run(website=123)
        self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)

    def test_only_website_skips_the_registry(self):
        registry, http = FakeRegistry(), site()
        result = run(registry, http, website="https://www.eksempel.no/om-oss")
        self.assertEqual(registry.calls, [])
        self.assertEqual(http.calls, [HTTPS_URL])
        self.assertIsNone(result.organization_number)
        self.assertIsNone(result.registry)
        self.assertIsNotNone(result.website)
        self.assertEqual([s.source_type for s in result.sources], ["website"])
        self.assertTrue(result.complete)

    def test_only_organization_number_uses_the_registered_website(self):
        http = site()
        result = run(FakeRegistry(make_company(website="www.eksempel.no")), http, organization_number=ORG)
        self.assertEqual(http.calls, [HTTPS_URL])
        self.assertEqual([s.source_type for s in result.sources], ["brreg", "website"])

    def test_only_organization_number_without_any_website(self):
        http = FakeHttp()
        result = run(FakeRegistry(make_company(website=None)), http, organization_number=ORG)
        self.assertEqual(http.calls, [])
        self.assertIsNone(result.website)
        self.assertEqual([s.source_type for s in result.sources], ["brreg"])
        self.assertTrue(result.complete)

    def test_given_website_wins_over_the_registered_one(self):
        http = FakeHttp({"https://annet.example.no/": page_response(GOOD_PAGE, url="https://annet.example.no/")})
        registry = FakeRegistry(make_company(website="www.eksempel.no"))
        run(registry, http, organization_number=ORG, website="annet.example.no")
        self.assertEqual(http.calls, ["https://annet.example.no/"])


class RegistryTests(unittest.TestCase):
    def test_registry_returns_none(self):
        http = site()
        result = run(FakeRegistry(None), http, organization_number=ORG, website="eksempel.no")
        self.assertEqual(result.warnings, ["registry_not_found"])
        self.assertFalse(result.complete)
        self.assertIsNone(result.registry)
        self.assertEqual(result.organization_number, ORG)
        self.assertEqual([s.source_type for s in result.sources], ["website"])

    def test_registry_none_and_no_website_gives_an_empty_result(self):
        result = run(FakeRegistry(None), organization_number=ORG)
        self.assertEqual(result.warnings, ["registry_not_found"])
        self.assertEqual(result.sources, [])
        self.assertIsNone(result.website)

    def test_retryable_error_then_success(self):
        sleeps: list[float] = []
        registry = FakeRegistry(unavailable(), make_company())
        result = run(registry, site(), organization_number=ORG, sleep=sleeps.append)
        self.assertEqual(registry.calls, [ORG, ORG])
        self.assertEqual(sleeps, [1.0])
        self.assertTrue(result.complete)
        self.assertIsNotNone(result.registry)
        self.assertEqual([s.source_type for s in result.sources], ["brreg", "website"])

    def test_retryable_error_exhausted_becomes_a_warning(self):
        sleeps: list[float] = []
        registry = FakeRegistry(*(AutomationError(ErrorType.RATE_LIMIT, "slow down") for _ in range(3)))
        result = run(registry, site(), organization_number=ORG, website="eksempel.no", sleep=sleeps.append)
        self.assertEqual(len(registry.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(result.warnings, ["registry_lookup_failed: rate_limit"])
        self.assertIsNone(result.registry)
        # The run goes on without registry data.
        self.assertIsNotNone(result.website)
        self.assertEqual([s.source_type for s in result.sources], ["website"])

    def test_warning_never_contains_the_error_message(self):
        secret = "token=abc123"
        registry = FakeRegistry(*(AutomationError(ErrorType.EXTERNAL_SERVICE_ERROR, secret) for _ in range(3)))
        result = run(registry, organization_number=ORG, sleep=lambda _: None)
        self.assertEqual(result.warnings, ["registry_lookup_failed: external_service_error"])
        self.assertNotIn(secret, " ".join(result.warnings))

    def test_non_retryable_error_propagates(self):
        for error_type in (ErrorType.AUTHENTICATION_ERROR, ErrorType.VALIDATION_ERROR, ErrorType.INTERNAL_ERROR):
            with self.subTest(error_type=error_type):
                registry = FakeRegistry(AutomationError(error_type, "stop"))
                sleeps: list[float] = []
                with self.assertRaises(AutomationError) as ctx:
                    run(registry, site(), organization_number=ORG, sleep=sleeps.append)
                self.assertEqual(ctx.exception.error_type, error_type)
                self.assertEqual(len(registry.calls), 1)
                self.assertEqual(sleeps, [])

    def test_other_exceptions_propagate(self):
        with self.assertRaises(RuntimeError):
            run(FakeRegistry(RuntimeError("bug")), organization_number=ORG)

    def test_missing_registry_source_url_is_a_warning_not_a_blank_source(self):
        result = run(FakeRegistry(make_company(source_url="")), site(), organization_number=ORG)
        self.assertEqual(result.warnings, ["registry_source_url_missing"])
        self.assertIsNotNone(result.registry)
        self.assertEqual([s.source_type for s in result.sources], ["website"])

    def test_unknown_employee_count_is_written_as_unknown(self):
        result = run(FakeRegistry(make_company(employee_count=None)), organization_number=ORG)
        self.assertIn("Antall ansatte: UNKNOWN", result.sources[0].note)

    def test_zero_employees_is_not_unknown(self):
        result = run(FakeRegistry(make_company(employee_count=0)), organization_number=ORG)
        self.assertIn("Antall ansatte: 0.", result.sources[0].note)

    def test_unknown_form_is_written_as_unknown(self):
        company = make_company(organization_form=None, organization_form_description=None)
        result = run(FakeRegistry(company), organization_number=ORG)
        self.assertIn("Organisasjonsform: UNKNOWN", result.sources[0].note)


class InactiveCompanyTests(unittest.TestCase):
    CASES = (
        ("bankrupt", dict(bankrupt=True), "bankrupt"),
        ("liquidation", dict(under_liquidation=True), "under_liquidation"),
        ("deleted", dict(deleted_date=date(2025, 6, 30)), "deleted"),
        ("both", dict(bankrupt=True, under_liquidation=True), "bankrupt, under_liquidation"),
    )

    def test_website_is_not_fetched(self):
        for label, overrides, reason in self.CASES:
            with self.subTest(label):
                http = site()
                result = run(FakeRegistry(make_company(**overrides)), http, organization_number=ORG)
                self.assertEqual(http.calls, [])
                self.assertIsNone(result.website)
                self.assertEqual(result.warnings, [f"inactive_company: {reason}"])
                self.assertFalse(result.complete)
                self.assertEqual(inactive_reason(result), reason)

    def test_given_website_is_not_fetched_either(self):
        http = site()
        run(FakeRegistry(make_company(bankrupt=True)), http, organization_number=ORG, website="eksempel.no")
        self.assertEqual(http.calls, [])

    def test_registry_source_is_kept_and_says_inactive(self):
        result = run(FakeRegistry(make_company(bankrupt=True)), organization_number=ORG)
        self.assertEqual([s.source_type for s in result.sources], ["brreg"])
        self.assertIn("Status: inaktiv (bankrupt)", result.sources[0].note)

    def test_inactive_without_any_website_still_warns(self):
        result = run(FakeRegistry(make_company(bankrupt=True, website=None)), organization_number=ORG)
        self.assertEqual(result.warnings, ["inactive_company: bankrupt"])

    def test_active_company_has_no_reason(self):
        result = run(FakeRegistry(make_company()), site(), organization_number=ORG)
        self.assertIsNone(inactive_reason(result))

    def test_no_registry_data_has_no_reason(self):
        self.assertIsNone(inactive_reason(ResearchResult()))


class WebsiteTests(unittest.TestCase):
    def test_bad_given_website_becomes_a_warning(self):
        http = FakeHttp()
        result = run(FakeRegistry(make_company()), http, organization_number=ORG, website="ikke en nettadresse")
        self.assertEqual(result.warnings, ["website_invalid"])
        self.assertEqual(http.calls, [])
        self.assertIsNone(result.website)
        # The registry part of the result is kept.
        self.assertEqual([s.source_type for s in result.sources], ["brreg"])

    def test_bad_registered_website_becomes_a_warning(self):
        result = run(FakeRegistry(make_company(website="ingen")), FakeHttp(), organization_number=ORG)
        self.assertEqual(result.warnings, ["website_invalid"])
        self.assertIsNotNone(result.registry)

    def test_only_bad_website_gives_an_empty_result_with_a_warning(self):
        result = run(website="http://")
        self.assertEqual(result.warnings, ["website_invalid"])
        self.assertEqual(result.sources, [])

    def test_unreachable_website_still_gives_a_result(self):
        http = FakeHttp()
        result = run(FakeRegistry(make_company()), http, organization_number=ORG)
        self.assertEqual(http.calls, [HTTPS_URL, HTTP_URL])
        self.assertFalse(result.website.reachable)
        self.assertEqual(result.warnings, [])
        self.assertEqual([s.source_type for s in result.sources], ["brreg", "website"])
        website_source = result.sources[1]
        self.assertEqual(website_source.url, HTTPS_URL)
        self.assertIn("kunne ikke hentes", website_source.note)

    def test_other_automation_errors_from_the_fetch_propagate(self):
        # fetch_and_analyze handles transport errors itself, so a non-validation AutomationError
        # can only come from a bug. It must stop the run instead of turning into a warning.
        for error_type in (ErrorType.INTERNAL_ERROR, ErrorType.AUTHENTICATION_ERROR, ErrorType.TEMPORARY_ERROR):
            with self.subTest(error_type=error_type):
                broken = mock.Mock(side_effect=AutomationError(error_type, "stop"))
                with mock.patch("src.research.company.fetch_and_analyze", broken):
                    with self.assertRaises(AutomationError) as ctx:
                        run(website="eksempel.no")
                self.assertEqual(ctx.exception.error_type, error_type)

    def test_validation_error_from_the_fetch_becomes_a_warning(self):
        broken = mock.Mock(side_effect=AutomationError(ErrorType.VALIDATION_ERROR, "bad url"))
        with mock.patch("src.research.company.fetch_and_analyze", broken):
            result = run(website="eksempel.no")
        self.assertEqual(result.warnings, ["website_invalid"])

    def test_other_errors_from_the_fetch_propagate(self):
        class BrokenHttp:
            def get(self, url, *, timeout=10.0, max_bytes=2_000_000):
                raise RuntimeError("bug")

        with self.assertRaises(RuntimeError):
            run(http=BrokenHttp(), website="eksempel.no")

    def test_website_without_observations_has_no_note(self):
        quiet = GOOD_PAGE.replace('<p><a href="mailto:post@eksempel.no">post@eksempel.no</a></p>', "")
        result = run(http=site(quiet), website="eksempel.no")
        self.assertEqual(result.website.observations, [])
        self.assertIsNone(result.sources[0].note)


class RateLimiterTests(unittest.TestCase):
    def test_limiter_runs_before_the_lookup_and_before_the_fetch(self):
        events: list[str] = []
        registry = FakeRegistry(make_company(), events=events)
        http = FakeHttp({HTTPS_URL: page_response(GOOD_PAGE)}, events=events)
        run(registry, http, organization_number=ORG, rate_limiter=FakeLimiter(events))
        self.assertEqual(events, ["wait", "lookup", "wait", "get"])

    def test_limiter_runs_before_every_lookup_attempt(self):
        events: list[str] = []
        registry = FakeRegistry(unavailable(), make_company(website=None), events=events)
        run(registry, organization_number=ORG, rate_limiter=FakeLimiter(events), sleep=lambda _: None)
        self.assertEqual(events, ["wait", "lookup", "wait", "lookup"])

    def test_limiter_is_not_used_for_a_fetch_that_never_happens(self):
        events: list[str] = []
        registry = FakeRegistry(make_company(bankrupt=True), events=events)
        run(registry, organization_number=ORG, rate_limiter=FakeLimiter(events))
        self.assertEqual(events, ["wait", "lookup"])

    def test_website_only_waits_once(self):
        events: list[str] = []
        http = FakeHttp({HTTPS_URL: page_response(GOOD_PAGE)}, events=events)
        run(http=http, website="eksempel.no", rate_limiter=FakeLimiter(events))
        self.assertEqual(events, ["wait", "get"])

    def test_real_rate_limiter_spaces_the_calls(self):
        sleeps: list[float] = []
        limiter = RateLimiter(5.0, clock=lambda: 100.0, sleep=sleeps.append)
        run(FakeRegistry(make_company()), site(), organization_number=ORG, rate_limiter=limiter)
        # The clock does not move, so the second call has to wait the full interval.
        self.assertEqual(sleeps, [5.0])


class ImprovementSignalTests(unittest.TestCase):
    def test_only_signals_are_returned(self):
        bare_with_email = BARE_PAGE.replace("Hei", 'Hei <a href="mailto:post@eksempel.no">post</a>')
        result = run(http=site(bare_with_email), website="eksempel.no")
        all_codes = [o.code for o in result.website.observations]
        self.assertIn("contact_email_found", all_codes)  # a fact, not a signal
        signals = improvement_signals(result)
        self.assertTrue(signals)
        self.assertTrue(all(o.improvement_signal for o in signals))
        self.assertNotIn("contact_email_found", [o.code for o in signals])
        self.assertEqual(
            {"missing_title", "missing_meta_description", "missing_viewport", "missing_h1", "no_cta_found"}
            - {o.code for o in signals},
            set(),
        )

    def test_good_page_has_no_signals(self):
        result = run(http=site(), website="eksempel.no")
        self.assertEqual(improvement_signals(result), [])

    def test_no_website_gives_no_signals(self):
        self.assertEqual(improvement_signals(ResearchResult()), [])

    def test_hand_built_result(self):
        website = run(http=site(), website="eksempel.no").website
        observations = [
            Observation("a", "Signal", HTTPS_URL, True),
            Observation("b", "Fakta", HTTPS_URL, False),
        ]
        result = ResearchResult(website=replace(website, observations=observations))
        self.assertEqual([o.code for o in improvement_signals(result)], ["a"])


class ResultToLeadTests(unittest.TestCase):
    def lead_from(self, company=None, **kwargs):
        result = run(FakeRegistry(company or make_company()), site(), organization_number=ORG)
        return result_to_lead(result, source="Brreg", **kwargs)

    def test_fields_come_from_the_registry(self):
        lead = self.lead_from()
        self.assertEqual(lead.company_name, "Eksempel Regnskap AS")
        self.assertEqual(lead.organization_number, ORG)
        self.assertEqual(lead.industry, "Regnskap")
        self.assertEqual(lead.employee_count, 12)
        self.assertEqual(lead.location, "Heimdal")
        self.assertEqual(lead.website, "www.eksempel.no")  # raw, as registered
        self.assertEqual(lead.source, "Brreg")

    def test_lead_starts_new_and_nothing_is_judged(self):
        lead = self.lead_from()
        self.assertEqual(lead.lead_status, LeadStatus.NEW)
        self.assertIsNone(lead.reason_for_fit)
        self.assertIsNone(lead.identified_problem)
        self.assertIsNone(lead.recommended_service)
        self.assertIsNone(lead.lead_score)
        self.assertIsNone(lead.notes)

    def test_unknown_values_stay_none(self):
        company = make_company(
            employee_count=None, industry_description=None, website=None, post_place=None, municipality=None
        )
        result = run(FakeRegistry(company), organization_number=ORG)
        lead = result_to_lead(result, source="Brreg")
        self.assertIsNone(lead.employee_count)
        self.assertIsNone(lead.industry)
        self.assertIsNone(lead.website)
        self.assertIsNone(lead.location)

    def test_zero_employees_is_kept(self):
        self.assertEqual(self.lead_from(make_company(employee_count=0)).employee_count, 0)

    def test_location_falls_back_to_municipality(self):
        self.assertEqual(self.lead_from(make_company(post_place=None)).location, "Trondheim")

    def test_no_contact_fields(self):
        lead = self.lead_from()
        self.assertIsNone(lead.contact_name)
        self.assertIsNone(lead.contact_role)
        self.assertIsNone(lead.contact_email)
        self.assertFalse(lead.contact_email_verified)

    def test_found_email_is_never_copied(self):
        result = run(FakeRegistry(make_company()), site(), organization_number=ORG)
        self.assertEqual(result.website.contact_emails, ["post@eksempel.no"])
        lead = result_to_lead(result, source="Brreg")
        self.assertIsNone(lead.contact_email)
        self.assertFalse(lead.contact_email_verified)
        self.assertNotIn("post@eksempel.no", repr(lead))

    def test_name_from_the_register_wins_over_the_argument(self):
        self.assertEqual(self.lead_from(company_name="Annet navn").company_name, "Eksempel Regnskap AS")

    def test_company_name_is_the_fallback(self):
        result = run(FakeRegistry(make_company(name=None)), organization_number=ORG)
        lead = result_to_lead(result, source="Brreg", company_name=" Eksempel Navn ")
        self.assertEqual(lead.company_name, "Eksempel Navn")
        website_only = run(http=site(), website="eksempel.no")
        lead = result_to_lead(website_only, source="Manuell", company_name="Eksempel Navn")
        self.assertEqual(lead.company_name, "Eksempel Navn")
        self.assertIsNone(lead.organization_number)

    def test_no_name_is_a_value_error(self):
        website_only = run(http=site(), website="eksempel.no")
        for result, kwargs in (
            (website_only, {}),
            (website_only, {"company_name": "  "}),
            (ResearchResult(), {}),
            (run(FakeRegistry(make_company(name=None)), organization_number=ORG), {}),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                result_to_lead(result, source="Brreg", **kwargs)

    def test_analysed_website_is_used_when_the_register_has_none(self):
        result = run(FakeRegistry(make_company(website=None)), site(), organization_number=ORG, website="eksempel.no")
        self.assertEqual(result_to_lead(result, source="Brreg").website, HTTPS_URL)

    def test_unusable_registered_website_is_left_out(self):
        result = run(FakeRegistry(make_company(website="ingen")), FakeHttp(), organization_number=ORG)
        self.assertIsNone(result_to_lead(result, source="Brreg").website)

    def test_lead_without_registry_uses_the_given_number(self):
        result = ResearchResult(organization_number=ORG)
        lead = result_to_lead(result, source="Manuell", company_name="Eksempel Navn")
        self.assertEqual(lead.organization_number, ORG)


class StoreResearchTests(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()

    def research(self) -> ResearchResult:
        return run(FakeRegistry(make_company()), site(), organization_number=ORG)

    def test_lead_from_result_can_be_added(self):
        lead = result_to_lead(self.research(), source="Brreg")
        lead_id = self.repo.add_lead(lead)
        stored = self.repo.get_lead(lead_id)
        self.assertEqual(stored.lead, lead)
        self.assertEqual(stored.lead.lead_status, LeadStatus.NEW)

    def test_sources_are_saved_and_readable(self):
        result = self.research()
        lead_id = self.repo.add_lead(result_to_lead(result, source="Brreg"))
        ids = store_research(self.repo, lead_id, result)
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(isinstance(i, int) for i in ids))
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(self.repo.list_research_sources(lead_id), result.sources)

    def test_sources_keep_type_time_and_note(self):
        result = self.research()
        lead_id = self.repo.add_lead(result_to_lead(result, source="Brreg"))
        store_research(self.repo, lead_id, result)
        registry_source, website_source = self.repo.list_research_sources(lead_id)
        self.assertEqual(registry_source.url, REGISTRY_URL)
        self.assertEqual(registry_source.source_type, "brreg")
        self.assertEqual(registry_source.retrieved_at, NOW)
        self.assertIn("Status: aktiv", registry_source.note)
        self.assertEqual(website_source.source_type, "website")
        self.assertEqual(website_source.note, result.sources[1].note)

    def test_result_without_sources_stores_nothing(self):
        lead_id = self.repo.add_lead(result_to_lead(self.research(), source="Brreg"))
        self.assertEqual(store_research(self.repo, lead_id, ResearchResult()), [])
        self.assertEqual(self.repo.list_research_sources(lead_id), [])

    def test_unknown_lead_is_refused(self):
        with self.assertRaises(LeadNotFoundError):
            store_research(self.repo, 999, self.research())

    def test_source_without_url_leaves_no_partial_rows(self):
        lead_id = self.repo.add_lead(result_to_lead(self.research(), source="Brreg"))
        bad = ResearchResult(sources=[ResearchSource("https://eksempel.no/", "website", NOW), ResearchSource(" ")])
        with self.assertRaises(LeadValidationError):
            store_research(self.repo, lead_id, bad)
        self.assertEqual(self.repo.list_research_sources(lead_id), [])

    def test_store_does_not_change_status(self):
        result = self.research()
        lead_id = self.repo.add_lead(result_to_lead(result, source="Brreg"))
        store_research(self.repo, lead_id, result)
        self.assertEqual(self.repo.get_lead(lead_id).lead.lead_status, LeadStatus.NEW)

    def test_inactive_company_result_can_be_stored(self):
        result = run(FakeRegistry(make_company(bankrupt=True)), organization_number=ORG)
        lead_id = self.repo.add_lead(result_to_lead(result, source="Brreg"))
        self.assertEqual(len(store_research(self.repo, lead_id, result)), 1)


if __name__ == "__main__":
    unittest.main()
