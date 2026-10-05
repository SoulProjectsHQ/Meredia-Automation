"""Internal lead score, 0 to 100. The score is internal and must never be sent to the customer.

The judgment per category (for example "is there a clear problem") comes from research,
possibly LLM assisted. The summing, range checks and classification are plain code.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum


class Priority(StrEnum):
    HIGH_PRIORITY = "HIGH_PRIORITY"  # 80-100
    GOOD_CANDIDATE = "GOOD_CANDIDATE"  # 60-79
    LOWER_PRIORITY = "LOWER_PRIORITY"  # 40-59
    DO_NOT_PRIORITIZE = "DO_NOT_PRIORITIZE"  # under 40


@dataclass(frozen=True)
class ScoreInput:
    size_fit: int = 0  # max 20: size fits the target group
    clear_problem: int = 0  # max 20: clear problem Meredia solves
    financial_capacity: int = 0  # max 15: likely ability to pay
    reachable_contact: int = 0  # max 15: decision maker or contact available
    professional_firm: int = 0  # max 10: professional services company
    growth_or_change: int = 0  # max 10: growth or change
    improvement_potential: int = 0  # max 10: website or digital setup can clearly improve


MAX_POINTS: dict[str, int] = {
    "size_fit": 20,
    "clear_problem": 20,
    "financial_capacity": 15,
    "reachable_contact": 15,
    "professional_firm": 10,
    "growth_or_change": 10,
    "improvement_potential": 10,
}


@dataclass(frozen=True)
class ScoreResult:
    total: int
    breakdown: dict[str, int]
    priority: Priority


def classify(score: int) -> Priority:
    if not 0 <= score <= 100:
        raise ValueError(f"Score must be between 0 and 100, got {score}")
    if score >= 80:
        return Priority.HIGH_PRIORITY
    if score >= 60:
        return Priority.GOOD_CANDIDATE
    if score >= 40:
        return Priority.LOWER_PRIORITY
    return Priority.DO_NOT_PRIORITIZE


def score_lead(inp: ScoreInput) -> ScoreResult:
    """Sum the categories. Points outside a category's range are rejected, not clamped."""
    breakdown: dict[str, int] = {}
    for f in fields(inp):
        value = getattr(inp, f.name)
        maximum = MAX_POINTS[f.name]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
            raise ValueError(f"{f.name} must be an integer between 0 and {maximum}, got {value!r}")
        breakdown[f.name] = value
    total = sum(breakdown.values())
    return ScoreResult(total=total, breakdown=breakdown, priority=classify(total))
