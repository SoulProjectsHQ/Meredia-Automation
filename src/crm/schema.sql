-- Meredia CRM schema (SQLite). One company has many contacts, leads, research sources.
-- One lead has many interactions, emails and tasks.
-- Email bodies are not stored here. Decide draft storage before building the email module.

CREATE TABLE IF NOT EXISTS company (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_number TEXT UNIQUE,
    name                TEXT NOT NULL,
    website             TEXT,
    domain              TEXT,
    industry            TEXT,
    employee_count      INTEGER CHECK (employee_count IS NULL OR employee_count >= 0),
    location            TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_company_domain ON company (domain);

CREATE TABLE IF NOT EXISTS contact (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    name           TEXT,
    role           TEXT,
    email          TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0, 1)),
    source         TEXT,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lead (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    contact_id          INTEGER REFERENCES contact (id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN (
        'NEW', 'RESEARCHED', 'QUALIFIED', 'READY_TO_CONTACT', 'CONTACTED',
        'FOLLOWUP_1', 'FOLLOWUP_2', 'REPLIED', 'INTERESTED', 'MEETING',
        'PROPOSAL', 'WON', 'LOST', 'DO_NOT_CONTACT')),
    score               INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    source              TEXT,
    reason_for_fit      TEXT,
    identified_problem  TEXT,
    recommended_service TEXT,
    first_contact_date  TEXT,
    followup_1_date     TEXT,
    followup_2_date     TEXT,
    last_contact_date   TEXT,
    next_action         TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lead_status ON lead (status);

CREATE TABLE IF NOT EXISTS interaction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES lead (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('email_sent', 'reply', 'call', 'meeting', 'note')),
    occurred_at TEXT NOT NULL,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS email (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id          INTEGER NOT NULL REFERENCES lead (id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('cold', 'followup_1', 'followup_2')),
    recipient        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVED', 'SENT')),
    template_version TEXT NOT NULL,
    message_id       TEXT,
    approved_by      TEXT,
    approved_at      TEXT,
    sent_at          TEXT,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- A mail can only be APPROVED or SENT with a named approver, and SENT needs a send time.
    CHECK (status = 'DRAFT' OR approved_by IS NOT NULL),
    CHECK (status != 'SENT' OR sent_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES lead (id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    due_date    TEXT,
    done        INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1))
);

CREATE TABLE IF NOT EXISTS research_source (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    url          TEXT NOT NULL,
    source_type  TEXT,
    retrieved_at TEXT NOT NULL,
    note         TEXT
);

-- Do-not-contact list. A row is written when a DO_NOT_CONTACT lead is deleted, so the same
-- company is not added again from another source. Identifiers only: no contact person, notes,
-- emails or sources. name_key is the normalized company name (see normalize_company_name).
CREATE TABLE IF NOT EXISTS suppression (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_number TEXT,
    domain              TEXT,
    name_key            TEXT,
    confirmed_by        TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (organization_number IS NOT NULL OR domain IS NOT NULL OR name_key IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppression_org ON suppression (organization_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppression_domain ON suppression (domain);
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppression_name ON suppression (name_key);
