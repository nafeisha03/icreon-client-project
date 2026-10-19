"""Shared test setup: one complete build of both datasets, made once per test run.

The build goes into a temporary folder, so the tests never touch the project's
own data folder. It takes a few seconds.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from calder_abm import evaluate, generate_data, pipeline


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> SimpleNamespace:
    root = tmp_path_factory.mktemp("calder_build")
    data = root / "data"
    generate_data.generate(data, quiet=True)
    messy, clean = root / "calder_abm.db", root / "calder_abm_clean.db"
    pipeline.run_dataset("messy", data / "raw", messy, quiet=True)
    pipeline.run_dataset("clean", data / "clean", clean, quiet=True)
    evaluate.run(messy, clean, data / "answer_key", quiet=True)
    return SimpleNamespace(root=root, data=data, raw=data / "raw", answer_key=data / "answer_key",
                           messy=messy, clean=clean)


@pytest.fixture(scope="session")
def messy_conn(built):
    conn = sqlite3.connect(built.messy)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def clean_conn(built):
    conn = sqlite3.connect(built.clean)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def truth(built) -> dict:
    with (built.answer_key / "accounts_truth.csv").open(newline="", encoding="utf-8") as handle:
        return {r["account_id"]: r for r in csv.DictReader(handle)}
