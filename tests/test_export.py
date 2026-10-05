import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.crm.db import connect, init_db
from src.crm.export import EXPORT_FIELDS, leads_to_csv, leads_to_json, write_export
from src.crm.models import Lead
from src.crm.repository import LeadRepository
from tests.test_repository import sample_lead


def repo_with_leads() -> LeadRepository:
    conn = connect(":memory:")
    init_db(conn)
    repo = LeadRepository(conn)
    repo.add_lead(sample_lead(first_contact_date=date(2026, 10, 6)))
    repo.add_lead(Lead(company_name="=HYPERLINK(\"http://ond.example\")", website="https://beta.no", notes="+47 123"))
    return repo


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.leads = repo_with_leads().list_leads()

    def test_json_has_all_fields_and_plain_values(self):
        rows = json.loads(leads_to_json(self.leads))
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0]), EXPORT_FIELDS)
        self.assertEqual(rows[0]["lead_status"], "NEW")
        self.assertEqual(rows[0]["first_contact_date"], "2026-10-06")
        self.assertIsNone(rows[1]["contact_email"])

    def test_csv_has_header_and_rows(self):
        rows = list(csv.DictReader(io.StringIO(leads_to_csv(self.leads))))
        self.assertEqual(list(rows[0]), EXPORT_FIELDS)
        self.assertEqual(rows[0]["company_name"], "Eksempel Regnskap AS")
        self.assertEqual(rows[0]["contact_email_verified"], "True")
        self.assertEqual(rows[1]["contact_email"], "")

    def test_csv_neutralizes_formulas(self):
        rows = list(csv.DictReader(io.StringIO(leads_to_csv(self.leads))))
        self.assertTrue(rows[1]["company_name"].startswith("'="))
        self.assertEqual(rows[1]["notes"], "'+47 123")

    def test_json_keeps_original_text(self):
        rows = json.loads(leads_to_json(self.leads))
        self.assertTrue(rows[1]["company_name"].startswith("="))

    def test_write_export_by_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = write_export(self.leads, Path(tmp) / "exports" / "leads.json")
            csv_path = write_export(self.leads, Path(tmp) / "leads.csv")
            self.assertEqual(len(json.loads(json_path.read_text(encoding="utf-8"))), 2)
            self.assertTrue(csv_path.read_text(encoding="utf-8").startswith("lead_id,"))
            with self.assertRaises(ValueError):
                write_export(self.leads, Path(tmp) / "leads.xlsx")

    def test_empty_export(self):
        self.assertEqual(json.loads(leads_to_json([])), [])
        self.assertEqual(leads_to_csv([]).strip(), ",".join(EXPORT_FIELDS))


if __name__ == "__main__":
    unittest.main()
