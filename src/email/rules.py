"""Mechanical checks on mail drafts. Word count and banned phrases are code, not LLM work."""

from __future__ import annotations

import re

COLD_MIN_WORDS = 70
COLD_MAX_WORDS = 140

BANNED_PHRASES = (
    "revolusjonerende",
    "markedsledende",
    "unik løsning",
    "game changer",
    "verdensklasse",
    "jeg håper denne mailen finner deg vel",
)


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def banned_phrases_in(text: str) -> list[str]:
    lowered = text.casefold()
    return [phrase for phrase in BANNED_PHRASES if phrase in lowered]


def check_cold_email(body: str) -> list[str]:
    """Return rule violations for a cold mail body. Empty list means it passes."""
    problems: list[str] = []
    words = word_count(body)
    if words < COLD_MIN_WORDS:
        problems.append(f"too short: {words} words, minimum {COLD_MIN_WORDS}")
    if words > COLD_MAX_WORDS:
        problems.append(f"too long: {words} words, maximum {COLD_MAX_WORDS}")
    problems.extend(f"banned phrase: {p}" for p in banned_phrases_in(body))
    return problems


def check_followup(body: str, first_mail_body: str) -> list[str]:
    """A follow-up must be shorter than the first mail and use no banned phrases."""
    problems: list[str] = []
    if word_count(body) >= word_count(first_mail_body):
        problems.append("follow-up must be shorter than the first mail")
    problems.extend(f"banned phrase: {p}" for p in banned_phrases_in(body))
    return problems
