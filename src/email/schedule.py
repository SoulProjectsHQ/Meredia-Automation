"""Follow-up timing and send days. Plain date logic, no LLM.

Preferred send days are Tuesday to Thursday. Friday, Saturday and Sunday are avoided for
cold sending. Monday evening can be used manually when needed, so it is not scheduled here.
"""

from __future__ import annotations

from datetime import date, timedelta

PREFERRED_WEEKDAYS = frozenset({1, 2, 3})  # Monday is 0
FIRST_FOLLOWUP_AFTER_DAYS = 4  # about 4 to 5 days after the first mail
SECOND_FOLLOWUP_AFTER_DAYS = 7  # about 7 to 10 days after the first follow-up
MAX_AUTOMATIC_FOLLOWUPS = 2


def is_preferred_send_day(day: date) -> bool:
    return day.weekday() in PREFERRED_WEEKDAYS


def next_send_day(day: date) -> date:
    """Return day itself if it is Tuesday to Thursday, otherwise the next such day."""
    while not is_preferred_send_day(day):
        day += timedelta(days=1)
    return day


def followup_1_date(first_mail_sent: date) -> date:
    return next_send_day(first_mail_sent + timedelta(days=FIRST_FOLLOWUP_AFTER_DAYS))


def followup_2_date(followup_1_sent: date) -> date:
    return next_send_day(followup_1_sent + timedelta(days=SECOND_FOLLOWUP_AFTER_DAYS))


def automatic_followup_allowed(followups_sent: int) -> bool:
    """A third cold follow-up needs a manual decision."""
    if followups_sent < 0:
        raise ValueError("followups_sent cannot be negative")
    return followups_sent < MAX_AUTOMATIC_FOLLOWUPS


def is_followup_due(due_date: date | None, today: date) -> bool:
    return due_date is not None and due_date <= today
