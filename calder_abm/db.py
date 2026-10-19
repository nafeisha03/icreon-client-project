"""Creates the SQLite databases from sql/schema.sql and handles saving and loading.

There is one database per dataset: data/calder_abm.db for the messy data (what
the dashboard shows) and data/calder_abm_clean.db for the clean twin.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

from . import config


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_database(path: str | Path) -> sqlite3.Connection:
    """Start a fresh database: delete any old file, build every table, load the reference lists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = connect(path)
    conn.executescript(config.project_path("schema").read_text(encoding="utf-8"))
    load_reference_lists(conn)
    return conn


def load_reference_lists(conn: sqlite3.Connection) -> None:
    """Copy the stages, the signal model and the review points from the config files."""
    insert(conn, "stages", [
        {
            "stage": s["stage"],
            "stage_order": s["order"],
            "kind": s["kind"],
            "firm_weight_pct": s.get("weight_pct"),
            "description": s["description"],
        }
        for s in config.stage_list()
    ])
    insert(conn, "signal_types", [
        {
            "signal_type": name,
            "label": info["label"],
            "category": info["category"],
            "direction": info["direction"],
            "strength": info["strength"],
            "supports_stage": info.get("supports_stage"),
            "explanation": info["explanation"],
        }
        for name, info in config.signal_types().items()
    ])
    insert(conn, "review_points", [
        {"as_of_date": date, "label": label} for date, label in config.review_points()
    ])
    conn.commit()


def insert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    """Insert a list of dicts.

    Rows are grouped by the columns they name, so a column a row leaves out
    keeps the database default instead of being set to empty.
    """
    rows = list(rows)
    groups: dict[tuple, list[tuple]] = {}
    for row in rows:
        columns = tuple(row.keys())
        groups.setdefault(columns, []).append(tuple(row[c] for c in columns))
    for columns, values in groups.items():
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        conn.executemany(sql, values)
    return len(rows)


def query(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def frame(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
