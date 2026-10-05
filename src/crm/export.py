"""Export leads to JSON or CSV. Exports hold business contact data, keep them under data/ (not in git)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import fields
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Iterable

from src.crm.models import Lead
from src.crm.repository import StoredLead

EXPORT_FIELDS = ["lead_id"] + [f.name for f in fields(Lead)]

# Spreadsheet programs run cells that start with these characters as formulas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _plain(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return value


def lead_to_row(stored: StoredLead) -> dict[str, object]:
    row: dict[str, object] = {"lead_id": stored.lead_id}
    for f in fields(Lead):
        row[f.name] = _plain(getattr(stored.lead, f.name))
    return row


def leads_to_json(leads: Iterable[StoredLead]) -> str:
    return json.dumps([lead_to_row(s) for s in leads], ensure_ascii=False, indent=2) + "\n"


def _csv_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def leads_to_csv(leads: Iterable[StoredLead]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for stored in leads:
        writer.writerow({k: _csv_cell(v) for k, v in lead_to_row(stored).items()})
    return buffer.getvalue()


def write_export(leads: Iterable[StoredLead], path: str | Path) -> Path:
    """Write leads to path. The format follows the suffix, .json or .csv."""
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".json":
        content = leads_to_json(leads)
    elif suffix == ".csv":
        content = leads_to_csv(leads)
    else:
        raise ValueError(f"Unsupported export format {suffix!r}, use .json or .csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
