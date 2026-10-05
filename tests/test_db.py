import re
import sqlite3
import unittest

from src.crm.db import SCHEMA_PATH, connect, init_db
from src.crm.models import InteractionKind, LeadStatus
from src.email.status import EmailKind, EmailStatus

TABLES = {"company", "contact", "lead", "interaction", "email", "task", "research_source"}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def add_company(self, name="Eksempel AS", org=None):
        cur = self.conn.execute("INSERT INTO company (name, organization_number) VALUES (?, ?)", (name, org))
        return cur.lastrowid

    def test_all_tables_exist(self):
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        self.assertTrue(TABLES <= {r["name"] for r in rows})

    def test_init_is_idempotent(self):
        init_db(self.conn)

    def test_one_company_many_contacts_and_leads(self):
        company = self.add_company()
        for name in ("Ola", "Kari"):
            self.conn.execute("INSERT INTO contact (company_id, name) VALUES (?, ?)", (company, name))
        for _ in range(2):
            self.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company,))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lead").fetchone()[0], 2)

    def test_org_number_is_unique_but_null_is_allowed_repeatedly(self):
        self.add_company("A", "912345678")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_company("B", "912345678")
        self.add_company("C")
        self.add_company("D")

    def test_lead_defaults_to_new(self):
        company = self.add_company()
        self.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company,))
        self.assertEqual(self.conn.execute("SELECT status FROM lead").fetchone()[0], "NEW")

    def test_invalid_status_and_score_rejected(self):
        company = self.add_company()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO lead (company_id, status) VALUES (?, 'MAYBE')", (company,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO lead (company_id, score) VALUES (?, 101)", (company,))

    def test_foreign_keys_enforced_and_cascade(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO lead (company_id) VALUES (999)")
        company = self.add_company()
        self.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company,))
        self.conn.execute("DELETE FROM company WHERE id = ?", (company,))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lead").fetchone()[0], 0)

    def test_email_defaults_to_draft(self):
        company = self.add_company()
        lead = self.conn.execute("INSERT INTO lead (company_id) VALUES (?)", (company,)).lastrowid
        self.conn.execute(
            "INSERT INTO email (lead_id, kind, recipient, template_version) VALUES (?, 'cold', 'a@b.no', 'v1')",
            (lead,),
        )
        self.assertEqual(self.conn.execute("SELECT status FROM email").fetchone()[0], "DRAFT")


class SchemaMatchesCodeTests(unittest.TestCase):
    """Guards against the SQL CHECK lists drifting from the enums."""

    def check_values(self, table, column):
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        block = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S).group(1)
        match = re.search(rf"{column}\s+TEXT[^,]*?CHECK \({column} IN \((.*?)\)\)", block, re.S)
        return set(re.findall(r"'([^']+)'", match.group(1)))

    def test_lead_statuses(self):
        self.assertEqual(self.check_values("lead", "status"), {s.value for s in LeadStatus})

    def test_email_statuses(self):
        self.assertEqual(self.check_values("email", "status"), {s.value for s in EmailStatus})

    def test_email_kinds(self):
        self.assertEqual(self.check_values("email", "kind"), {k.value for k in EmailKind})

    def test_interaction_kinds(self):
        self.assertEqual(self.check_values("interaction", "kind"), {k.value for k in InteractionKind})


if __name__ == "__main__":
    unittest.main()
