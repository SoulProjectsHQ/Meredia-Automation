import dataclasses
import json
import unittest
from datetime import date

from src.automation.errors import AutomationError, ErrorType
from src.integrations.brreg import DEFAULT_BASE_URL, BrregClient, CompanyRegistry, RegistryCompany
from src.integrations.http import HttpResponse
from tests.test_validation import make_org_number, valid_org_number

SECRET = "SECRET-BODY-TEXT"


def other_org_number() -> str:
    """A second synthetic valid org number, different from valid_org_number()."""
    for n in range(91234600, 91234700):
        candidate = make_org_number(str(n))
        if candidate and candidate != valid_org_number():
            return candidate
    raise AssertionError("no second valid synthetic org number found")


def reply(status: int, body: object) -> HttpResponse:
    """Build a response. A dict or list is dumped as JSON, a string is used as is."""
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return HttpResponse(url="", status=status, body=text, elapsed_ms=5, size_bytes=len(text))


class FakeHttp:
    """HttpClient fake. Hands back the canned response or raises the canned error, and records calls."""

    def __init__(self, response: HttpResponse | None = None, error: AutomationError | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float = 10.0, max_bytes: int = 2_000_000) -> HttpResponse:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def full_body(number: str) -> dict:
    return {
        "organisasjonsnummer": number,
        "navn": "Eksempel Regnskap AS",
        "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
        "registreringsdatoEnhetsregisteret": "2015-03-12",
        "naeringskode1": {"kode": "69.201", "beskrivelse": "Regnskapstjenester"},
        "antallAnsatte": 12,
        "hjemmeside": "www.eksempel.no",
        "forretningsadresse": {
            "land": "Norge",
            "kommune": "TRONDHEIM",
            "poststed": "TRONDHEIM",
            "postnummer": "7010",
            "adresse": ["Eksempelgata 1"],
        },
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
        "_links": {"self": {"href": "https://example.no/x"}},
    }


def lookup(body: object, status: int = 200, number: str | None = None) -> RegistryCompany | None:
    number = number or valid_org_number()
    return BrregClient(FakeHttp(reply(status, body))).lookup(number)


def lookup_error(body: object, status: int = 200, number: str | None = None) -> AutomationError:
    """Run lookup and return the AutomationError it raises. Fails if nothing is raised."""
    try:
        lookup(body, status, number)
    except AutomationError as error:
        return error
    raise AssertionError("lookup did not raise AutomationError")


class FoundCompanyTests(unittest.TestCase):
    def test_full_response(self):
        number = valid_org_number()
        http = FakeHttp(reply(200, full_body(number)))
        company = BrregClient(http).lookup(number)

        url = f"{DEFAULT_BASE_URL}/enheter/{number}"
        self.assertEqual(http.calls, [url])
        self.assertEqual(
            company,
            RegistryCompany(
                organization_number=number,
                name="Eksempel Regnskap AS",
                organization_form="AS",
                organization_form_description="Aksjeselskap",
                industry_code="69.201",
                industry_description="Regnskapstjenester",
                employee_count=12,
                website="www.eksempel.no",
                municipality="TRONDHEIM",
                post_place="TRONDHEIM",
                registered_date=date(2015, 3, 12),
                bankrupt=False,
                under_liquidation=False,
                deleted_date=None,
                source_url=url,
            ),
        )
        self.assertTrue(company.is_active)

    def test_minimal_response(self):
        number = valid_org_number()
        company = lookup({"organisasjonsnummer": number, "navn": "Eksempel AS"})
        self.assertEqual(company.organization_number, number)
        self.assertEqual(company.name, "Eksempel AS")
        for name in (
            "organization_form",
            "organization_form_description",
            "industry_code",
            "industry_description",
            "employee_count",
            "website",
            "municipality",
            "post_place",
            "registered_date",
            "deleted_date",
        ):
            self.assertIsNone(getattr(company, name), name)
        self.assertFalse(company.bankrupt)
        self.assertFalse(company.under_liquidation)
        self.assertTrue(company.is_active)
        self.assertTrue(company.source_url.endswith(f"/enheter/{number}"))

    def test_empty_object_gives_unknown_everything(self):
        company = lookup({})
        self.assertIsNone(company.name)
        self.assertEqual(company.organization_number, valid_org_number())

    def test_missing_employees_is_unknown_and_zero_is_kept(self):
        body = full_body(valid_org_number())
        del body["antallAnsatte"]
        self.assertIsNone(lookup(body).employee_count)
        body["antallAnsatte"] = 0
        self.assertEqual(lookup(body).employee_count, 0)

    def test_website_is_kept_raw(self):
        body = full_body(valid_org_number())
        for raw in ("www.eksempel.no", "Eksempel.NO/Om-Oss/", "http://www.eksempel.no", "https://eksempel.no:8080/x?y=1"):
            body["hjemmeside"] = raw
            self.assertEqual(lookup(body).website, raw)

    def test_date_with_time_part_is_accepted(self):
        body = full_body(valid_org_number())
        body["registreringsdatoEnhetsregisteret"] = "2015-03-12T00:00:00"
        self.assertEqual(lookup(body).registered_date, date(2015, 3, 12))

    def test_invalid_dates_are_unknown(self):
        body = full_body(valid_org_number())
        for bad in ("2015-13-45", "12.03.2015", "", "yesterday", 20150312, None, ["2015-03-12"]):
            body["registreringsdatoEnhetsregisteret"] = bad
            self.assertIsNone(lookup(body).registered_date, repr(bad))


class StatusTests(unittest.TestCase):
    def test_bankrupt_is_not_active(self):
        body = full_body(valid_org_number())
        body["konkurs"] = True
        company = lookup(body)
        self.assertTrue(company.bankrupt)
        self.assertFalse(company.under_liquidation)
        self.assertFalse(company.is_active)

    def test_under_liquidation_is_not_active(self):
        for key in ("underAvvikling", "underTvangsavviklingEllerTvangsopplosning"):
            with self.subTest(key=key):
                body = full_body(valid_org_number())
                body[key] = True
                company = lookup(body)
                self.assertTrue(company.under_liquidation)
                self.assertFalse(company.bankrupt)
                self.assertFalse(company.is_active)

    def test_deleted_unit_from_410(self):
        number = valid_org_number()
        body = {"organisasjonsnummer": number, "navn": "Slettet Eksempel AS", "slettedato": "2021-06-30"}
        company = lookup(body, status=410, number=number)
        self.assertEqual(company.organization_number, number)
        self.assertEqual(company.name, "Slettet Eksempel AS")
        self.assertEqual(company.deleted_date, date(2021, 6, 30))
        self.assertFalse(company.is_active)
        self.assertIsNone(company.employee_count)
        self.assertTrue(company.source_url.endswith(f"/enheter/{number}"))

    def test_deleted_date_on_200_also_means_inactive(self):
        body = full_body(valid_org_number())
        body["slettedato"] = "2021-06-30"
        company = lookup(body)
        self.assertEqual(company.deleted_date, date(2021, 6, 30))
        self.assertFalse(company.is_active)

    def test_410_without_usable_deleted_date_is_an_error(self):
        number = valid_org_number()
        for body in (
            {"organisasjonsnummer": number, "navn": "Slettet AS"},
            {"organisasjonsnummer": number, "slettedato": "ikke en dato"},
            {"organisasjonsnummer": number, "slettedato": 20210630},
        ):
            with self.subTest(body=body):
                error = lookup_error(body, status=410, number=number)
                self.assertEqual(error.error_type, ErrorType.EXTERNAL_SERVICE_ERROR)

    def test_410_with_bad_body_is_an_error(self):
        self.assertEqual(lookup_error("", status=410).error_type, ErrorType.EXTERNAL_SERVICE_ERROR)
        self.assertEqual(lookup_error([], status=410).error_type, ErrorType.EXTERNAL_SERVICE_ERROR)

    def test_not_found_returns_none(self):
        http = FakeHttp(reply(404, {"feilmelding": "Ikke funnet"}))
        self.assertIsNone(BrregClient(http).lookup(valid_org_number()))
        self.assertEqual(len(http.calls), 1)

    def test_not_found_does_not_need_a_body(self):
        self.assertIsNone(lookup("", status=404))


class NumberValidationTests(unittest.TestCase):
    def test_invalid_numbers_raise_and_make_no_request(self):
        valid = valid_org_number()
        wrong_check = valid[:8] + str((int(valid[8]) + 1) % 10)
        for bad in ("", "123", "12345678a", wrong_check, None, 912345678, "../../x"):
            with self.subTest(bad=bad):
                http = FakeHttp(reply(200, {}))
                with self.assertRaises(AutomationError) as ctx:
                    BrregClient(http).lookup(bad)
                self.assertEqual(ctx.exception.error_type, ErrorType.VALIDATION_ERROR)
                self.assertFalse(ctx.exception.retryable)
                self.assertEqual(http.calls, [])

    def test_spaces_and_prefix_are_normalized(self):
        number = valid_org_number()
        spaced = f"{number[:3]} {number[3:6]} {number[6:]}"
        for raw in (spaced, f"NO{number}", f"NO {spaced} MVA", f" {number} "):
            with self.subTest(raw=raw):
                http = FakeHttp(reply(200, full_body(number)))
                company = BrregClient(http).lookup(raw)
                self.assertEqual(http.calls, [f"{DEFAULT_BASE_URL}/enheter/{number}"])
                self.assertEqual(company.organization_number, number)

    def test_base_url_is_configurable_and_trailing_slash_is_ignored(self):
        number = valid_org_number()
        http = FakeHttp(reply(404, ""))
        BrregClient(http, base_url="https://test.example.no/api/").lookup(number)
        self.assertEqual(http.calls, [f"https://test.example.no/api/enheter/{number}"])


class BadResponseTests(unittest.TestCase):
    def assert_service_error(self, error: AutomationError) -> None:
        self.assertEqual(error.error_type, ErrorType.EXTERNAL_SERVICE_ERROR)
        self.assertTrue(error.retryable)
        self.assertNotIn(SECRET, str(error))

    def test_malformed_json(self):
        for body in (f"<html>{SECRET}", f'{{"navn": "{SECRET}"', "", "   ", "NaN-ish {"):
            with self.subTest(body=body):
                self.assert_service_error(lookup_error(body))

    def test_non_object_json(self):
        for body in (f'["{SECRET}"]', f'"{SECRET}"', "42", "null", "true"):
            with self.subTest(body=body):
                self.assert_service_error(lookup_error(body))

    def test_deeply_nested_json_does_not_crash(self):
        self.assert_service_error(lookup_error("[" * 100_000 + "]" * 100_000))

    def test_mismatched_number(self):
        other = other_org_number()
        self.assertNotEqual(other, valid_org_number())
        body = full_body(other)
        body["navn"] = SECRET
        self.assert_service_error(lookup_error(body))
        self.assert_service_error(lookup_error(body, status=410))

    def test_unusable_returned_number_is_a_mismatch(self):
        for returned in ("", "abc", "12345"):
            with self.subTest(returned=returned):
                body = full_body(valid_org_number())
                body["organisasjonsnummer"] = returned
                self.assert_service_error(lookup_error(body))

    def test_returned_number_with_spaces_matches(self):
        number = valid_org_number()
        body = full_body(number)
        body["organisasjonsnummer"] = f"{number[:3]} {number[3:6]} {number[6:]}"
        self.assertEqual(lookup(body).organization_number, number)

    def test_unexpected_status(self):
        for status in (301, 500, 503):
            with self.subTest(status=status):
                self.assert_service_error(lookup_error(full_body(valid_org_number()), status=status))

    def test_http_client_errors_pass_through_without_retry(self):
        error = AutomationError(ErrorType.TEMPORARY_ERROR, "timeout")
        http = FakeHttp(error=error)
        with self.assertRaises(AutomationError) as ctx:
            BrregClient(http).lookup(valid_org_number())
        self.assertIs(ctx.exception, error)
        self.assertEqual(len(http.calls), 1)

    def test_wrong_types_in_descriptive_fields_give_unknown(self):
        number = valid_org_number()
        body = {
            "organisasjonsnummer": number,
            "navn": 12345,
            "organisasjonsform": "AS",
            "registreringsdatoEnhetsregisteret": 20150312,
            "naeringskode1": ["69.201"],
            "antallAnsatte": "12",
            "hjemmeside": ["www.eksempel.no"],
            "forretningsadresse": "Eksempelgata 1",
        }
        company = lookup(body)
        self.assertEqual(company, RegistryCompany(number, None, source_url=company.source_url))
        self.assertTrue(company.is_active)

    def test_wrong_types_inside_nested_objects_give_unknown(self):
        body = full_body(valid_org_number())
        body["organisasjonsform"] = {"kode": 5, "beskrivelse": None}
        body["naeringskode1"] = {"kode": 69.201, "beskrivelse": ["x"]}
        body["forretningsadresse"] = {"kommune": 1601, "poststed": {}}
        company = lookup(body)
        self.assertIsNone(company.organization_form)
        self.assertIsNone(company.organization_form_description)
        self.assertIsNone(company.industry_code)
        self.assertIsNone(company.industry_description)
        self.assertIsNone(company.municipality)
        self.assertIsNone(company.post_place)

    def test_bad_employee_counts_are_unknown(self):
        body = full_body(valid_org_number())
        for bad in (-1, True, 12.5, None, "12", [12]):
            body["antallAnsatte"] = bad
            self.assertIsNone(lookup(body).employee_count, repr(bad))

    def test_blank_strings_are_unknown_and_text_is_trimmed(self):
        body = full_body(valid_org_number())
        body["navn"] = "  Eksempel AS  "
        body["hjemmeside"] = "   "
        company = lookup(body)
        self.assertEqual(company.name, "Eksempel AS")
        self.assertIsNone(company.website)

    def test_null_flags_are_false(self):
        body = full_body(valid_org_number())
        body["konkurs"] = None
        body["underAvvikling"] = None
        company = lookup(body)
        self.assertFalse(company.bankrupt)
        self.assertTrue(company.is_active)

    def test_wrong_typed_status_flag_is_an_error_not_a_guess(self):
        for key in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning"):
            for bad in ("true", "ja", 1, 0, [], {}):
                with self.subTest(key=key, bad=bad):
                    body = full_body(valid_org_number())
                    body[key] = bad
                    self.assert_service_error(lookup_error(body))


class ModelTests(unittest.TestCase):
    def test_company_is_frozen(self):
        company = lookup(full_body(valid_org_number()))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            company.name = "Annet AS"

    def test_company_holds_no_personal_data_fields(self):
        names = {f.name for f in dataclasses.fields(RegistryCompany)}
        self.assertEqual(
            names,
            {
                "organization_number",
                "name",
                "organization_form",
                "organization_form_description",
                "industry_code",
                "industry_description",
                "employee_count",
                "website",
                "municipality",
                "post_place",
                "registered_date",
                "bankrupt",
                "under_liquidation",
                "deleted_date",
                "source_url",
            },
        )

    def test_unlisted_keys_in_the_body_are_not_carried_over(self):
        body = full_body(valid_org_number())
        body["roller"] = [{"navn": SECRET}]
        body["stifter"] = SECRET
        company = lookup(body)
        self.assertNotIn(SECRET, repr(company))

    def test_is_active_needs_all_three_conditions(self):
        base = RegistryCompany(organization_number=valid_org_number(), name="Eksempel AS")
        self.assertTrue(base.is_active)
        self.assertFalse(dataclasses.replace(base, bankrupt=True).is_active)
        self.assertFalse(dataclasses.replace(base, under_liquidation=True).is_active)
        self.assertFalse(dataclasses.replace(base, deleted_date=date(2021, 6, 30)).is_active)

    def test_client_fits_the_registry_protocol(self):
        def find(registry: CompanyRegistry, number: str) -> RegistryCompany | None:
            return registry.lookup(number)

        self.assertIsNone(find(BrregClient(FakeHttp(reply(404, ""))), valid_org_number()))


if __name__ == "__main__":
    unittest.main()
