"""Reads every source file and stores each row exactly as received.

Nothing is cleaned or changed here. Each row is kept as JSON with its source,
file name, row number and, when the export says so, the date it reached us.
Keeping the untouched copy means any later result can be traced back to the
row it came from.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from . import config, db


def read_rows(path: Path) -> list[dict]:
    """All rows of a CSV, JSON-lines or JSON file, as dictionaries of text."""
    if path.suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError(f"Unsupported file type: {path.name}")


def ingest_all(conn: sqlite3.Connection, data_dir: Path) -> list[dict]:
    """Store every row of every source listed in config/source_mappings.yaml.

    Returns the stored rows (with their new raw_record_id) for the next step.
    A missing file is reported and skipped, not treated as a crash.
    """
    records: list[dict] = []
    next_id = 1
    for key, spec in config.source_mappings().items():
        path = Path(data_dir) / spec["file"]
        if not path.exists():
            print(f"  warning: {spec['file']} not found in {data_dir}; skipped")
            continue
        received_column = spec.get("received_column")
        for row_number, row in enumerate(read_rows(path), start=1):
            received = (row.get(received_column) or None) if received_column else None
            records.append({
                "raw_record_id": next_id,
                "source": spec["source"],
                "source_file": spec["file"],
                "source_row": row_number,
                "received_at": received,
                "payload_json": json.dumps(row, ensure_ascii=False),
                # kept in memory only, for the cleaning step:
                "_key": key,
                "_row": row,
            })
            next_id += 1
    db.insert(conn, "raw_records", [
        {k: v for k, v in r.items() if not k.startswith("_")} for r in records])
    conn.commit()
    return records
