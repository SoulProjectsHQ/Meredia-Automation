import unittest

from src.crm.dedupe import find_duplicate, normalize_company_name
from src.crm.models import Lead
from tests.test_validation import valid_org_number


class NormalizeNameTests(unittest.TestCase):
    def test_drops_legal_suffix_and_punctuation(self):
        self.assertEqual(normalize_company_name("Eksempel Regnskap AS"), "eksempel regnskap")
        self.assertEqual(normalize_company_name("Eksempel Regnskap A/S"), "eksempel regnskap")
        self.assertEqual(normalize_company_name("  EKSEMPEL   regnskap ans "), "eksempel regnskap")

    def test_keeps_names_that_only_look_like_suffixes_inside(self):
        self.assertEqual(normalize_company_name("As Rådgivning"), "as rådgivning")

    def test_empty(self):
        self.assertIsNone(normalize_company_name(""))
        self.assertIsNone(normalize_company_name(None))
        self.assertIsNone(normalize_company_name("AS"))


class FindDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.org = valid_org_number()
        self.existing = [
            Lead(company_name="Alfa Advokater AS", organization_number=self.org, website="https://alfa.no"),
            Lead(company_name="Beta Regnskap AS", website="https://www.beta.no"),
        ]

    def test_matches_on_org_number_even_if_name_differs(self):
        candidate = Lead(company_name="Helt annet navn", organization_number=f"NO {self.org} MVA")
        match = find_duplicate(candidate, self.existing)
        self.assertEqual(match.matched_on, "organization_number")
        self.assertEqual(match.existing.company_name, "Alfa Advokater AS")

    def test_matches_on_domain(self):
        candidate = Lead(company_name="Beta Group", website="http://beta.no/kontakt")
        self.assertEqual(find_duplicate(candidate, self.existing).matched_on, "domain")

    def test_matches_on_name(self):
        candidate = Lead(company_name="BETA regnskap")
        self.assertEqual(find_duplicate(candidate, self.existing).matched_on, "company_name")

    def test_org_number_wins_over_domain_match_in_other_lead(self):
        candidate = Lead(company_name="X", organization_number=self.org, website="https://beta.no")
        match = find_duplicate(candidate, self.existing)
        self.assertEqual(match.matched_on, "organization_number")
        self.assertEqual(match.existing.company_name, "Alfa Advokater AS")

    def test_no_match(self):
        candidate = Lead(company_name="Gamma Rådgivning AS", website="https://gamma.no")
        self.assertIsNone(find_duplicate(candidate, self.existing))

    def test_missing_identifiers_never_match_each_other(self):
        existing = [Lead(company_name="Delta AS")]
        candidate = Lead(company_name="Epsilon AS")
        self.assertIsNone(find_duplicate(candidate, existing))


if __name__ == "__main__":
    unittest.main()
