"""One chronological timeline per account, across all its people.

The database view account_timeline does the joining. This module adds the
review-point rule: a replay "as of" a date sees only records that had both
happened and reached us by the end of that day.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import db


def cutoff(as_of: str) -> str:
    """The last moment of a review day, in the same format as the stored times."""
    return f"{as_of}T23:59:59Z"


def visible(signal: dict, as_of: str) -> bool:
    end = cutoff(as_of)
    return bool(signal.get("occurred_at")) and signal["occurred_at"] <= end and (signal.get("received_at") or signal["occurred_at"]) <= end


def by_account(signals: list[dict], as_of: str, include_duplicates: bool = False) -> dict[str, list[dict]]:
    """Signals each account had, as known at the review point, in date order."""
    out: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        if not s.get("account_id") or not visible(s, as_of):
            continue
        if s.get("is_duplicate") and not include_duplicates:
            continue
        out[s["account_id"]].append(s)
    for items in out.values():
        items.sort(key=lambda s: (s["occurred_at"], s["signal_id"]))
    return out


def account_timeline(conn: sqlite3.Connection, account_id: str, as_of: str | None = None) -> list[dict]:
    """The saved timeline for one account, optionally as known at a review point."""
    rows = db.query(conn, "SELECT * FROM account_timeline WHERE account_id = ? ORDER BY occurred_at", (account_id,))
    if as_of:
        rows = [r for r in rows if visible(r, as_of)]
    return rows
