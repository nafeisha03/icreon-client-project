"""Sentence helpers that turn rule results into readable reasons.

Everything the managing partner reads is built here from facts and rule IDs,
so the wording stays consistent and never adds a fact of its own.
"""
from __future__ import annotations

from datetime import date, datetime


def as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def day(value) -> str:
    """'2026-09-22T14:00:00Z' -> 'Sep 22, 2026'."""
    d = as_date(value)
    return f"{d:%b} {d.day}, {d.year}" if d else "an unknown date"


def short_day(value) -> str:
    d = as_date(value)
    return f"{d:%b} {d.day}" if d else "an unknown date"


def days_between(earlier, later) -> int | None:
    a, b = as_date(earlier), as_date(later)
    return (b - a).days if a and b else None


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def money(usd) -> str:
    if usd is None:
        return "unknown"
    usd = float(usd)
    if usd >= 1_000_000:
        return f"${usd / 1_000_000:.1f}M"
    if usd >= 1_000:
        return f"${usd / 1_000:.0f}K"
    return f"${usd:,.0f}"


def join_words(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def count_of(part: int, whole: int, what: str) -> str:
    return f"{part} of {whole} {what}"


def cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
