import unittest
from datetime import date

from src.crm.models import Lead
from src.crm.validation import (
    is_valid_email_format,
    is_valid_org_number,
    normalize_domain,
    normalize_org_number,
    validate_lead,
)


def make_org_number(prefix8: str) -> str:
    """Build a synthetic valid org number from 8 digits. Returns '' if no check digit exists."""
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(d) * w for d, w in zip(prefix8, weights))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return "" if check == 10 else prefix8 + str(check)


def valid_org_number() -> str:
    for n in range(91234500, 91234600):
        candidate = make_org_number(str(n))
        if candidate:
            return candidate
    raise AssertionError("no valid synthetic org number found")


class OrgNumberTests(unittest.TestCase):
    def test_valid_number_passes(self):
        self.assertTrue(is_valid_org_number(valid_org_number()))

    def test_wrong_check_digit_fails(self):
        valid = valid_org_number()
        wrong = valid[:8] + str((int(valid[8]) + 1) % 10)
        self.assertFalse(is_valid_org_number(wrong))

    def test_wrong_length_and_letters_fail(self):
        self.assertFalse(is_valid_org_number("12345678"))
        self.assertFalse(is_valid_org_number("12345678a"))
        self.assertFalse(is_valid_org_number(None))
        self.assertFalse(is_valid_org_number(""))

    def test_normalizes_spaces_prefix_and_suffix(self):
        valid = valid_org_number()
        spaced = f"{valid[:3]} {valid[3:6]} {valid[6:]}"
        self.assertEqual(normalize_org_number(spaced), valid)
        self.assertEqual(normalize_org_number(f"NO {spaced} MVA"), valid)
        self.assertTrue(is_valid_org_number(f"NO{valid}MVA"))


class EmailAndDomainTests(unittest.TestCase):
    def test_email_format(self):
        self.assertTrue(is_valid_email_format("ola@eksempel.no"))
        self.assertTrue(is_valid_email_format("ola.nordmann+salg@sub.eksempel.no"))
        self.assertFalse(is_valid_email_format("ola@eksempel"))
        self.assertFalse(is_valid_email_format("ola eksempel.no"))
        self.assertFalse(is_valid_email_format(None))

    def test_normalize_domain(self):
        self.assertEqual(normalize_domain("https://www.Eksempel.no/om-oss?x=1"), "eksempel.no")
        self.assertEqual(normalize_domain("eksempel.no"), "eksempel.no")
        self.assertEqual(normalize_domain("http://eksempel.no:8080"), "eksempel.no")
        self.assertEqual(normalize_domain("www.sub.eksempel.no"), "sub.eksempel.no")

    def test_normalize_domain_rejects_garbage(self):
        self.assertIsNone(normalize_domain(""))
        self.assertIsNone(normalize_domain(None))
        self.assertIsNone(normalize_domain("ikke en url"))
        self.assertIsNone(normalize_domain("localhost"))


class ValidateLeadTests(unittest.TestCase):
    def test_minimal_valid_lead(self):
        self.assertEqual(validate_lead(Lead(company_name="Eksempel AS")), [])

    def test_full_valid_lead(self):
        lead = Lead(
            company_name="Eksempel AS",
            organization_number=valid_org_number(),
            website="https://eksempel.no",
            contact_email="post@eksempel.no",
            lead_score=72,
            employee_count=12,
            first_contact_date=date(2026, 10, 6),
            followup_1_date=date(2026, 10, 13),
            followup_2_date=date(2026, 10, 20),
        )
        self.assertEqual(validate_lead(lead), [])

    def test_collects_all_problems(self):
        lead = Lead(
            company_name=" ",
            organization_number="123",
            website="ikke en url",
            contact_email="feil",
            lead_score=120,
            employee_count=-1,
        )
        self.assertEqual(len(validate_lead(lead)), 6)

    def test_verified_flag_needs_email(self):
        lead = Lead(company_name="Eksempel AS", contact_email_verified=True)
        self.assertIn("contact_email_verified is set but contact_email is missing", validate_lead(lead))

    def test_followup_dates_must_be_ordered(self):
        lead = Lead(
            company_name="Eksempel AS",
            first_contact_date=date(2026, 10, 6),
            followup_1_date=date(2026, 10, 1),
        )
        self.assertIn("followup_1_date is before first_contact_date", validate_lead(lead))
        lead = Lead(company_name="Eksempel AS", followup_2_date=date(2026, 10, 20))
        self.assertIn("followup_2_date is set without followup_1_date", validate_lead(lead))


if __name__ == "__main__":
    unittest.main()
