"""Shared helpers for the dashboard pages: reading the results and showing labels.

The dashboard only reads. Every number and sentence on it was written to the
database by the pipeline (python -m calder_abm.pipeline); nothing is worked out
here except sorting, filtering and formatting.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import streamlit as st

from calder_abm import config
from calder_abm.text import day, money, plural

DATASETS = {"Messy data": "messy_db", "Clean twin": "clean_db"}

KIND_COLORS = {"KNOWN": "green", "INFERRED": "blue", "UNKNOWN": "orange"}
KIND_HELP = {
    "KNOWN": "Recorded directly in a source system, with its date and source.",
    "INFERRED": "Worked out from known facts by a written rule; the rule is named.",
    "UNKNOWN": "Not in the data. Someone has to find out.",
}
CONFIDENCE_COLORS = {"High": "green", "Medium": "blue", "Low": "orange", "Insufficient evidence": "red"}
VERDICT_COLORS = {"Increase": "green", "Continue": "blue", "Reduce": "orange", "Stop": "red",
                  "Insufficient evidence": "gray"}
EVIDENCE_COLORS = {"Consistent pattern": "green", "Association": "blue", "No clear difference": "gray",
                   "Insufficient evidence": "gray"}
TIER_COLORS = {"Act now": "red", "Find out": "violet", "Push": "orange", "Nurture": "blue",
               "Reduce spend": "gray", "Won": "green"}
TIER_ORDER = ["Act now", "Find out", "Push", "Nurture", "Reduce spend", "Won"]
TIER_HELP = {
    "Act now": "A decision is close, or a follow-up is overdue.",
    "Find out": "The evidence is contradictory or too thin to judge; confirm the real status first.",
    "Push": "Moving forward, a new trigger, or a current client with no AI conversation yet.",
    "Nurture": "Keep in light-touch marketing.",
    "Reduce spend": "Stalled for 90+ days, lost, or reached for over a sales cycle with no engagement.",
    "Won": "Signed: hand over to delivery.",
}


# -- what the sidebar selected ----------------------------------------------------
def selection() -> tuple[Path, str, str, str]:
    """(database path, review date, review label, dataset name) chosen in the sidebar."""
    dataset = st.session_state.get("dataset") or "Messy data"
    labels = dict(config.review_points())
    as_of = st.session_state.get("as_of") or config.latest_review_point()
    return config.project_path(DATASETS[dataset]), as_of, labels[as_of], dataset


def header(title: str, intro: str | None = None) -> None:
    _, as_of, label, dataset = selection()
    st.title(title)
    st.caption(f"{label}, {day(as_of)} · {dataset} · Only records that had arrived by that day are used.")
    if intro:
        st.markdown(intro)


# -- reading the database ---------------------------------------------------------
def _version(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(ttl=3600, max_entries=300, show_spinner=False)
def _query(path: str, version: float, sql: str, params: tuple) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def query(sql: str, params: tuple = (), path: Path | None = None) -> pd.DataFrame:
    """Run a read-only query against the selected database (or another one)."""
    path = path or selection()[0]
    return _query(str(path), _version(path), sql, tuple(params))


def metric(subject_type: str, subject_id: str, name: str, as_of: str | None = None,
           window: int | None = None, path: Path | None = None) -> dict | None:
    as_of = as_of or selection()[1]
    window_sql = "window_days IS NULL" if window is None else "window_days = ?"
    params = (as_of, subject_type, subject_id, name) + (() if window is None else (window,))
    rows = query(f"SELECT value, sample_size, note FROM measurements WHERE as_of_date = ? AND subject_type = ? "
                 f"AND subject_id = ? AND metric = ? AND {window_sql}", params, path)
    return rows.iloc[0].to_dict() if len(rows) else None


def metric_value(subject_type: str, subject_id: str, name: str, **kw):
    found = metric(subject_type, subject_id, name, **kw)
    if not found or found["value"] is None or pd.isna(found["value"]):
        return None
    return found["value"]


def accounts_at(as_of: str | None = None) -> pd.DataFrame:
    """Every account with its assessment and next action at the review point."""
    as_of = as_of or selection()[1]
    return query("""
        SELECT a.account_id, a.company_name, a.industry, a.target_tier, a.existing_relationship, a.account_owner,
               s.current_stage, s.stage_note, s.last_active_stage, s.trend, s.trend_reason, s.confidence,
               s.confidence_reasons, s.attention_tier, s.days_since_meaningful_activity,
               s.open_opportunity_value_usd, s.usable_signal_count,
               r.recommendation_id, r.rule_id, r.action, r.target_role, r.target_contact_id, r.channel, r.reason,
               r.confidence AS action_confidence, r.priority_rank
        FROM account_assessments s
        JOIN accounts a USING (account_id)
        LEFT JOIN recommendations r ON r.account_id = s.account_id AND r.as_of_date = s.as_of_date
        WHERE s.as_of_date = ?
        ORDER BY r.priority_rank
    """, (as_of,))


def stage_names() -> list[str]:
    return [s["stage"] for s in config.stage_list()]


# -- showing labels -----------------------------------------------------------------
def md(text) -> str:
    """Escape characters that Markdown would otherwise treat as formatting (such as $ for maths)."""
    out = str(text if text is not None else "")
    for ch in "\\`*_[]<>$~|#":
        out = out.replace(ch, "\\" + ch)
    return out


def badge(text: str, color: str) -> str:
    return f":{color}-badge[{text}]"


def kind_badge(kind: str) -> str:
    return badge(kind, KIND_COLORS.get(kind, "gray"))


def confidence_badge(level: str | None) -> str:
    if not level:
        return badge("No confidence level", "gray")
    return badge(f"Confidence: {level}" if level != "Insufficient evidence" else "Insufficient evidence",
                 CONFIDENCE_COLORS.get(level, "gray"))


def verdict_badge(verdict: str) -> str:
    return badge(verdict, VERDICT_COLORS.get(verdict, "gray"))


def tier_badge(tier: str) -> str:
    return badge(tier, TIER_COLORS.get(tier, "gray"))


def statement_line(kind: str, text: str, confidence: str | None = None) -> None:
    extra = f" {confidence_badge(confidence)}" if confidence and kind != "KNOWN" else ""
    st.markdown(f"{kind_badge(kind)}{extra} {md(text)}")


def reasons_text(raw) -> str:
    try:
        items = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        items = [str(raw)]
    return "; ".join(items)


def legend() -> None:
    """The three labels every statement carries."""
    with st.expander("How to read the labels", icon=":material/info:"):
        for kind in ["KNOWN", "INFERRED", "UNKNOWN"]:
            st.markdown(f"{kind_badge(kind)} {KIND_HELP[kind]}")
        st.markdown(f"{badge('Insufficient evidence', 'red')} The evidence is too thin or contradictory to say "
                    "anything; the system says so instead of guessing.")
        st.markdown("Marketing results are always **association, not proof**: no comparison group was deliberately "
                    "left out, so no result here shows that a program caused a sale.")


__all__ = ["DATASETS", "selection", "header", "query", "metric", "metric_value", "accounts_at", "md", "badge",
           "kind_badge", "confidence_badge", "verdict_badge", "tier_badge", "statement_line", "reasons_text",
           "legend", "day", "money", "plural", "stage_names", "TIER_ORDER", "TIER_HELP"]
