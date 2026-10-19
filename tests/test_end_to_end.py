"""Milestone 10: the whole prototype, from nothing, exactly as a new user would run it.

Copies the project (without its data) to a temporary folder, then runs the three
commands from the README there: generate the data, build the databases, and open
every dashboard page. Skip it with:  python -m pytest -m "not slow"
"""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP = shutil.ignore_patterns("data", "__pycache__", ".pytest_cache", "*.db", ".git")

PAGE_CHECK = """
from streamlit.testing.v1 import AppTest
pages = ["dashboard/account_detail.py", "dashboard/executive_summary.py", "dashboard/marketing.py",
         "dashboard/then_and_now.py", "dashboard/data_quality.py"]
at = AppTest.from_file("app.py", default_timeout=120).run()
assert not at.exception, at.exception
for page in pages:
    at.switch_page(page).run()
    assert not at.exception, (page, at.exception)
print("all pages opened")
"""


def run(args, cwd):
    done = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return done.stdout


@pytest.mark.slow
def test_a_fresh_copy_runs_from_raw_files_to_dashboard(tmp_path):
    project = tmp_path / "calder-hollis-abm"
    shutil.copytree(ROOT, project, ignore=SKIP)
    assert not (project / "data").exists()

    out = run(["-m", "calder_abm.generate_data"], project)
    assert (project / "data" / "raw" / "crm_contacts.csv").exists()
    assert len(list((project / "data" / "raw").iterdir())) == 15

    out = run(["-m", "calder_abm.pipeline"], project)
    assert "Evaluation against the answer key" in out
    with sqlite3.connect(project / "data" / "calder_abm.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 120
        assert conn.execute("SELECT COUNT(*) FROM evaluation_results WHERE metric = "
                            "'high_confidence_links_wrong' AND value > 0").fetchone()[0] == 0
    assert (project / "data" / "calder_abm_clean.db").exists()

    assert "all pages opened" in run(["-c", PAGE_CHECK], project)
