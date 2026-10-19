"""Milestone 9: every dashboard page opens without an error, for both datasets and every review point.

The pages run headless (no browser) against the test build, so the project's
own data folder is never needed.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from calder_abm import config

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PAGES = ["dashboard/overview.py", "dashboard/account_detail.py", "dashboard/executive_summary.py",
         "dashboard/marketing.py", "dashboard/then_and_now.py", "dashboard/data_quality.py"]


@pytest.fixture
def app(built, monkeypatch):
    paths = config.settings()["paths"]
    monkeypatch.setitem(paths, "messy_db", str(built.messy))
    monkeypatch.setitem(paths, "clean_db", str(built.clean))

    def open_page(page: str, dataset: str = "Messy data", as_of: str | None = None) -> AppTest:
        at = AppTest.from_file(APP, default_timeout=60)
        at.session_state["dataset"] = dataset
        at.session_state["as_of"] = as_of or config.latest_review_point()
        at.run()
        if page != PAGES[0]:
            at.switch_page(page).run()
        return at
    return open_page


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("dataset", ["Messy data", "Clean twin"])
def test_pages_open_at_the_latest_review_point(app, page, dataset):
    at = app(page, dataset)
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_pages_open_at_the_six_month_review(app, page):
    at = app(page, as_of=config.review_points()[0][0])
    assert not at.exception, at.exception


def test_the_pipeline_page_shows_the_goal_and_the_attention_list(app):
    at = app(PAGES[0])
    labels = [m.label for m in at.metric]
    assert "Signed engagements" in labels and "Records to review" in labels
    assert len(at.dataframe) >= 1


def test_the_account_page_follows_the_selected_account(app):
    at = app(PAGES[1])
    at.selectbox(key="account_id").set_value("ACC-001").run()
    assert not at.exception
    assert at.title[0].value == "ABC Corp"
    text = " ".join(m.value for m in at.markdown)
    assert "General Counsel" in text


def test_the_app_explains_how_to_build_missing_databases(monkeypatch, tmp_path):
    paths = config.settings()["paths"]
    monkeypatch.setitem(paths, "messy_db", str(tmp_path / "missing.db"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.error and "not been built" in at.error[0].value
