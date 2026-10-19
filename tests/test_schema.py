"""The database builds from sql/schema.sql and enforces its own rules.

Loads a small ABC Corp example with two review points, reads the account
timeline, then tries bad writes that the database must refuse.
"""
import json
import sqlite3

import pytest

from calder_abm import config, db


@pytest.fixture()
def conn(tmp_path):
    connection = db.create_database(tmp_path / "test.db")
    yield connection
    connection.close()


def load_abc_corp(conn):
    """A valid example: ABC Corp, Aware at month 6 and Proposal / RFP at month 24."""
    ins = db.insert
    ins(conn, "pipeline_runs", [{"dataset_variant": "messy", "started_at": "2026-10-01T09:00:00Z"}])
    ins(conn, "accounts", [{
        "account_id": "ACC-001", "company_name": "ABC Corp", "domain": "abccorp.com",
        "industry": "Insurance", "company_size": "1,000-4,999", "location": "Hartford, CT",
        "country": "US", "account_owner": "R. Okafor", "target_tier": 1,
        "existing_relationship": "None", "current_stage": "Proposal / RFP"}])
    ins(conn, "contacts", [
        {"contact_id": "CON-0102", "account_id": "ACC-001", "name": "Jane Smith",
         "email": "j.smith@abccorp.com", "job_title": "Head of Risk", "seniority": "Director",
         "buying_role": "Influencer", "match_confidence": "High"},
        {"contact_id": "CON-0107", "account_id": "ACC-001", "name": "Luis Ortega",
         "email": None, "job_title": "General Counsel", "seniority": "C-level",
         "buying_role": "Legal Reviewer", "match_confidence": "Medium"}])
    ins(conn, "campaigns", [{"campaign_id": "CMP-01", "name": "AI Controls Webinar Series",
                             "channel": "Webinar", "start_date": "2024-11-01", "planned_budget_usd": 90000}])
    ins(conn, "marketing_spend", [{"campaign_id": "CMP-01", "month": "2025-03",
                                   "category": "Media", "amount_usd": 4000}])
    ins(conn, "opportunities", [{"opportunity_id": "OPP-012", "account_id": "ACC-001",
                                 "name": "AI governance assessment", "estimated_value_usd": 350000,
                                 "crm_stage": "Proposal / RFP", "status": "Open",
                                 "created_date": "2026-06-10"}])
    payload = json.dumps({"email": " J.Smith@ABCCorp.com ", "date": "04/03/2025"})
    ins(conn, "raw_records", [
        {"source": "events", "source_file": "event_attendance.csv", "source_row": 2,
         "received_at": "2025-03-13T08:00:00Z", "payload_json": payload},
        {"source": "website", "source_file": "web_visits.csv", "source_row": 7,
         "received_at": "2026-09-01T08:00:00Z", "payload_json": "{}"},
        {"source": "notes", "source_file": "meeting_notes.jsonl", "source_row": 3,
         "received_at": "2026-06-02T08:00:00Z", "payload_json": "{}"}])
    ins(conn, "identity_matches", [
        {"raw_record_id": 1, "input_company_name": "ABC Corporation",
         "input_email": " J.Smith@ABCCorp.com ", "matched_account_id": "ACC-001",
         "match_confidence": "High", "match_reason": "email domain abccorp.com matches ABC Corp",
         "decision": "Linked", "matched_contact_id": "CON-0102"},
        {"raw_record_id": 2, "input_company_name": "Meridian Health",
         "matched_account_id": None, "match_confidence": "Low",
         "match_reason": "name matches 2 companies", "decision": "Needs review",
         "candidate_account_ids": '["ACC-014", "ACC-031"]'}])
    ins(conn, "signals", [
        {"signal_id": "SIG-00001", "raw_record_id": 1, "account_id": "ACC-001",
         "contact_id": "CON-0102", "campaign_id": "CMP-01", "occurred_at": "2025-03-04T19:00:00Z",
         "received_at": "2025-03-13T08:00:00Z", "arrived_late": 1, "source": "events",
         "signal_type": "webinar_registered", "strength": "Weak", "sentiment": "Positive",
         "match_confidence": "High"},
        {"signal_id": "SIG-00002", "raw_record_id": 3, "account_id": "ACC-001",
         "contact_id": "CON-0102", "occurred_at": "2026-06-01T15:00:00Z",
         "received_at": "2026-06-02T08:00:00Z", "source": "notes",
         "signal_type": "need_identified", "strength": "Strong", "sentiment": "Positive",
         "match_confidence": "High", "extraction_method": "Phrase rule",
         "evidence_quote": "The board wants an AI controls review before year end."},
        {"signal_id": "SIG-00003", "account_id": "ACC-001", "contact_id": "CON-0107",
         "occurred_at": "2026-09-02T14:00:00Z", "received_at": "2026-09-02T18:00:00Z",
         "source": "crm", "signal_type": "proposal_requested", "strength": "Very strong",
         "sentiment": "Positive", "match_confidence": "Medium"},
        {"signal_id": "SIG-00004", "account_id": "ACC-001", "contact_id": "CON-0107",
         "occurred_at": "2026-09-02T14:00:00Z", "received_at": "2026-09-03T09:00:00Z",
         "source": "meetings", "signal_type": "proposal_requested", "strength": "Very strong",
         "sentiment": "Positive", "match_confidence": "High", "is_duplicate": 1,
         "duplicate_of": "SIG-00003"},
        {"signal_id": "SIG-00005", "raw_record_id": 2, "account_id": None,
         "occurred_at": "2026-08-30T10:00:00Z", "received_at": "2026-09-01T08:00:00Z",
         "source": "website", "signal_type": "website_visit", "strength": "Weak",
         "match_confidence": "Low"}])
    ins(conn, "account_assessments", [
        {"account_id": "ACC-001", "as_of_date": "2025-03-31", "current_stage": "Aware",
         "trend": "Steady", "trend_reason": "Registered for a webinar on Mar 4",
         "confidence": "Low", "attention_tier": "Nurture", "usable_signal_count": 1},
        {"account_id": "ACC-001", "as_of_date": "2026-09-30", "current_stage": "Proposal / RFP",
         "trend": "Progressing", "trend_reason": "Proposal requested on Sep 2",
         "confidence": "Medium", "attention_tier": "Act now", "usable_signal_count": 3}])
    ins(conn, "recommendations", [{
        "recommendation_id": "REC-2026-09-30-ACC-001", "account_id": "ACC-001",
        "as_of_date": "2026-09-30", "rule_id": "A8", "target_role": "Decision Maker",
        "action": "Offer a 30-minute walkthrough of the proposal", "channel": "Partner outreach",
        "reason": "Proposal requested on Sep 2.", "confidence": "Medium",
        "created_at": "2026-10-01T09:00:00Z"}])
    ins(conn, "recommendation_statements", [
        {"recommendation_id": "REC-2026-09-30-ACC-001", "kind": "KNOWN",
         "statement": "ABC Corp asked for a proposal on Sep 2 (CRM).", "evidence_refs": '["SIG-00003"]'},
        {"recommendation_id": "REC-2026-09-30-ACC-001", "kind": "INFERRED",
         "statement": "ABC Corp is at Proposal / RFP.", "rule_id": "STG-PROPOSAL"},
        {"recommendation_id": "REC-2026-09-30-ACC-001", "kind": "UNKNOWN",
         "statement": "We do not know whether budget is approved."}])
    ins(conn, "outcome_comparisons", [{
        "as_of_date": "2026-09-30", "activity": "CMP-03", "activity_kind": "campaign",
        "comparison": "Signed vs lost", "among": "closed accounts that reached Proposal / RFP",
        "group_a_with": 8, "group_a_total": 14, "group_b_with": 3, "group_b_total": 12,
        "came_before_progress": 1, "could_be_chance": 0, "evidence_level": "Association",
        "statement": "8 of 14 signed accounts attended a roundtable; 3 of 12 lost accounts did."}])
    ins(conn, "budget_recommendations", [{
        "as_of_date": "2026-09-30", "subject_type": "campaign", "subject_id": "CMP-01",
        "verdict": "Continue", "evidence_level": "Association", "reason": "Example",
        "accounts_reached": 20}])
    conn.commit()


def test_schema_builds_all_tables(conn):
    tables = db.table_names(conn)
    assert len(tables) == 23
    for required in ["accounts", "contacts", "signals", "opportunities", "campaigns",
                     "recommendations", "stage_history", "identity_matches"]:
        assert required in tables
    views = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")]
    assert views == ["account_timeline"]


def test_reference_lists_load_from_config(conn):
    stages = db.query(conn, "SELECT stage FROM stages ORDER BY stage_order")
    assert [s["stage"] for s in stages] == [s["stage"] for s in config.stage_list()]
    assert len(stages) == 10
    count = conn.execute("SELECT COUNT(*) FROM signal_types").fetchone()[0]
    assert count == len(config.signal_types())
    points = db.query(conn, "SELECT as_of_date FROM review_points ORDER BY as_of_date")
    assert [p["as_of_date"] for p in points] == [d for d, _ in config.review_points()]


def test_valid_example_and_timeline(conn):
    load_abc_corp(conn)
    rows = db.query(conn, "SELECT signal_id, signal_label, evidence_quote FROM account_timeline "
                          "WHERE account_id = 'ACC-001'")
    # Duplicate (SIG-00004) and the unconfirmed visit (SIG-00005) are left out.
    assert [r["signal_id"] for r in rows] == ["SIG-00001", "SIG-00002", "SIG-00003"]
    assert rows[1]["evidence_quote"].startswith("The board wants")
    stages = db.query(conn, "SELECT as_of_date, current_stage FROM account_assessments "
                            "WHERE account_id = 'ACC-001' ORDER BY as_of_date")
    assert [s["current_stage"] for s in stages] == ["Aware", "Proposal / RFP"]


BAD_WRITES = {
    "stage that is not on the list":
        "UPDATE accounts SET current_stage = 'Proposal Review' WHERE account_id = 'ACC-001'",
    "confidence written as a percentage":
        "UPDATE account_assessments SET confidence = '72%' WHERE account_id = 'ACC-001'",
    "firm weight above 100%":
        "UPDATE stages SET firm_weight_pct = 120 WHERE stage = 'Signed Agreement'",
    "'Linked' with only Medium confidence":
        "UPDATE identity_matches SET match_confidence = 'Medium' WHERE match_id = 1",
    "review case that still carries a company":
        "UPDATE identity_matches SET matched_account_id = 'ACC-001' WHERE match_id = 2",
    "'Linked' record with no company":
        "UPDATE identity_matches SET matched_account_id = NULL WHERE match_id = 1",
    "low-confidence signal attached to a company":
        "UPDATE signals SET account_id = 'ACC-001' WHERE signal_id = 'SIG-00005'",
    "signal read from a note without its sentence":
        "UPDATE signals SET evidence_quote = NULL WHERE signal_id = 'SIG-00002'",
    "budget increase resting on Insufficient evidence":
        "UPDATE budget_recommendations SET verdict = 'Increase', evidence_level = 'Insufficient evidence'",
    "Insufficient evidence verdict with a stronger evidence level":
        "UPDATE budget_recommendations SET verdict = 'Insufficient evidence', evidence_level = 'Association'",
    "KNOWN statement with no evidence":
        "INSERT INTO recommendation_statements (recommendation_id, kind, statement) "
        "VALUES ('REC-2026-09-30-ACC-001', 'KNOWN', 'Budget is approved.')",
    "INFERRED statement with no rule":
        "INSERT INTO recommendation_statements (recommendation_id, kind, statement) "
        "VALUES ('REC-2026-09-30-ACC-001', 'INFERRED', 'They will sign soon.')",
    "loss reason on a won deal":
        "UPDATE opportunities SET status = 'Won', loss_reason = 'Chose another firm'",
    "comparison claiming 15 of 14 accounts":
        "UPDATE outcome_comparisons SET group_a_with = 15",
    "signal pointing at a company that does not exist":
        "UPDATE signals SET account_id = 'ACC-999' WHERE signal_id = 'SIG-00001'",
    "unknown signal type":
        "UPDATE signals SET signal_type = 'vibes_good' WHERE signal_id = 'SIG-00001'",
    "channel outside the list":
        "UPDATE recommendations SET channel = 'Carrier pigeon'",
    "second recommendation for the same account and review point":
        "INSERT INTO recommendations (recommendation_id, account_id, as_of_date, rule_id, target_role, "
        "action, channel, reason, confidence, created_at) VALUES ('REC-X', 'ACC-001', '2026-09-30', 'A1', "
        "'x', 'x', 'Email', 'x', 'Low', '2026-10-01')",
    "assessment for a date that is not a review point":
        "INSERT INTO account_assessments (account_id, as_of_date, current_stage, trend, trend_reason, "
        "confidence, attention_tier) VALUES ('ACC-001', '2026-01-01', 'Aware', 'Steady', 'x', 'Low', 'Nurture')",
    "evidence level that is not on the list":
        "UPDATE outcome_comparisons SET evidence_level = 'Proven'",
}


@pytest.mark.parametrize("label", list(BAD_WRITES))
def test_bad_write_is_refused(conn, label):
    load_abc_corp(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(BAD_WRITES[label])
