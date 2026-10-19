"""Small helpers shared by the tests."""
from __future__ import annotations

import sqlite3

from calder_abm import config


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()[0]


def latest() -> str:
    return config.latest_review_point()


def first() -> str:
    return config.review_points()[0][0]

