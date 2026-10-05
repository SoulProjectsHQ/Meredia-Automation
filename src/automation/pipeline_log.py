"""Readable pipeline log. Secrets and tokens are redacted before anything is written.

Entry format:
    2026-10-05 10:31
    DISCOVERY
    Found: Eksempel AS
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Stage(StrEnum):
    DISCOVERY = "DISCOVERY"
    RESEARCH = "RESEARCH"
    VALIDATION = "VALIDATION"
    SCORING = "SCORING"
    QUALIFICATION = "QUALIFICATION"
    CONTACT = "CONTACT"
    EMAIL = "EMAIL"
    SEND = "SEND"
    FOLLOWUP = "FOLLOWUP"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"


_REDACTIONS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd)(\s*[=:]\s*)\S+"),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[REDACTED]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def format_entry(when: datetime, stage: Stage | str, message: str) -> str:
    one_line = " ".join(redact(message).split())
    return f"{when:%Y-%m-%d %H:%M}\n{stage}\n{one_line}\n"


class PipelineLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, stage: Stage | str, message: str, now: datetime | None = None) -> str:
        entry = format_entry(now or datetime.now(), stage, message)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return entry
