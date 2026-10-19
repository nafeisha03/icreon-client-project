"""Milestone 5: signals, timelines and stages.

The first tests feed the stage rules small hand-made timelines; the rest check
the full build, including the ABC Corp example from the design document.
"""
import json

from calder_abm import config
from calder_abm.stages import assess
from calder_abm.timeline import by_account, visible
from helpers import latest, one, rows


def sig(n, kind, when, received=None, match="High", account="ACC-X"):
    return {"signal_id": f"S{n:03d}", "account_id": account, "signal_type": kind,
            "occurred_at": f"{when}T12:00:00Z", "received_at": f"{received or when}T12:00:00Z",
            "strength": config.signal_types()[kind].get("strength", "None"), "match_confidence": match,
            "description": kind, "contact_id": None, "is_duplicate": 0}


JOURNEY = [sig(1, "website_visit", "2025-01-10"), sig(2, "webinar_attended", "2025-02-12"),
           sig(3, "need_identified", "2025-03-20"), sig(4, "discovery_meeting", "2025-04-02"),
           sig(5, "proposal_requested", "2025-05-15"), sig(6, "verbal_commitment", "2025-06-20")]


def test_the_stage_is_the_furthest_step_with_evidence():
    r = assess(JOURNEY, "2025-06-30")
    assert r.stage == "Verbal Commitment"
    assert [h["to_stage"] for h in r.history] == ["Aware", "Interested", "Qualified Lead", "Active Conversation",
                                                  "Proposal / RFP", "Verbal Commitment"]
    assert all(h["evidence_signal_ids"] for h in r.history)         # every move names its evidence
    assert r.trend == "Progressing"


def test_a_review_point_sees_only_what_had_happened_by_then():
    assert assess(JOURNEY, "2025-03-31").stage == "Qualified Lead"
    assert assess([], "2025-03-31").stage == "Target"


def test_45_quiet_days_in_the_pipeline_means_stalled_and_activity_resumes_it():
    r = assess(JOURNEY[:4], "2025-07-01")                     # last meaningful activity on Apr 2
    assert r.stage == "Stalled" and r.last_active_stage == "Active Conversation"
    assert r.trend == "Stalled"
    resumed = assess(JOURNEY[:4] + [sig(9, "follow_up_meeting", "2025-06-25")], "2025-07-01")
    assert resumed.stage == "Active Conversation"
    assert "STG-RESUMED" in [h["rule_id"] for h in resumed.history]


def test_quiet_marketing_stages_never_stall():
    assert assess(JOURNEY[:2], "2025-12-31").stage == "Interested"


def test_a_verbal_commitment_can_still_fall_through():
    warned = assess(JOURNEY + [sig(7, "budget_cut", "2025-06-25")], "2025-06-30")
    assert warned.stage == "Verbal Commitment" and warned.stage_note == "warning: budget cut or freeze"
    lost = assess(JOURNEY + [sig(8, "opportunity_lost", "2025-07-10")], "2025-07-31")
    assert lost.stage == "Lost" and lost.last_active_stage == "Verbal Commitment"


def test_our_own_outreach_and_news_never_move_the_stage():
    r = assess([sig(1, "outreach_sent", "2025-01-10"), sig(2, "ad_shown", "2025-02-01"),
                sig(3, "news_trigger", "2025-02-15")], "2025-03-31")
    assert r.stage == "Target"


def test_a_skipped_step_is_allowed_but_noted():
    r = assess([sig(1, "rfp_received", "2025-03-01")], "2025-03-31")
    assert r.stage == "Proposal / RFP"
    assert r.gaps == ["a proposal with no meeting on record"]


def test_a_crm_stage_with_no_supporting_evidence_is_a_conflict():
    r = assess(JOURNEY[:4], "2025-04-30", crm_stage="Verbal Commitment")
    assert r.conflicts == []                                   # not quiet long enough yet
    r = assess(JOURNEY[:3], "2025-06-30", crm_stage="Verbal Commitment")
    assert r.conflicts and "The CRM says Verbal Commitment" in r.conflicts[0]


def test_late_records_are_used_only_after_they_arrive():
    late = sig(1, "need_identified", "2025-03-28", received="2025-04-06")
    assert not visible(late, "2025-03-31") and visible(late, "2025-04-30")
    assert by_account([late], "2025-03-31") == {}
    assert by_account([dict(late, is_duplicate=1)], "2025-04-30") == {}


# -- the full build ----------------------------------------------------------------------------
def test_abc_corp_matches_the_design_document_example(messy_conn):
    a = rows(messy_conn, "SELECT * FROM account_assessments WHERE account_id = 'ACC-001' AND as_of_date = ?",
             (latest(),))[0]
    assert a["current_stage"] == "Proposal / RFP" and a["stage_note"] == "legal reviewing"
    legal = rows(messy_conn, "SELECT * FROM account_timeline WHERE account_id = 'ACC-001' "
                             "AND signal_type = 'legal_joined'")
    assert legal and legal[0]["buying_role"] == "Legal Reviewer"


def test_every_stage_move_points_to_real_signals(messy_conn):
    ids = {r[0] for r in messy_conn.execute("SELECT signal_id FROM signals")}
    for h in rows(messy_conn, "SELECT * FROM stage_history"):
        for signal_id in json.loads(h["evidence_signal_ids"]):
            assert signal_id in ids


def test_duplicates_are_kept_but_left_out_of_the_timeline(messy_conn):
    duplicates = one(messy_conn, "SELECT COUNT(*) FROM signals WHERE is_duplicate = 1")
    assert duplicates > 0
    total = one(messy_conn, "SELECT COUNT(*) FROM signals WHERE account_id IS NOT NULL AND occurred_at IS NOT NULL")
    in_view = one(messy_conn, "SELECT COUNT(*) FROM account_timeline")
    assert in_view == total - one(messy_conn, "SELECT COUNT(*) FROM signals WHERE is_duplicate = 1 "
                                              "AND account_id IS NOT NULL AND occurred_at IS NOT NULL")


def test_automatic_email_opens_are_not_counted(messy_conn):
    skipped = rows(messy_conn, "SELECT raw_record_id FROM data_quality_issues WHERE details LIKE 'Opened within a minute%'")
    assert skipped
    ids = tuple(r["raw_record_id"] for r in skipped[:200])
    marks = ",".join("?" * len(ids))
    assert one(messy_conn, f"SELECT COUNT(*) FROM signals WHERE raw_record_id IN ({marks})", ids) == 0


def test_hedged_notes_become_questions_not_signals(messy_conn):
    hedged = one(messy_conn, "SELECT COUNT(*) FROM data_quality_issues WHERE issue_type = 'hedged_statement'")
    assert hedged > 0
    quotes = [r[0] for r in messy_conn.execute("SELECT evidence_quote FROM signals WHERE evidence_quote IS NOT NULL")]
    assert not any(" might " in q or " may have " in q for q in quotes)


def test_stalled_accounts_have_been_quiet_for_45_days(messy_conn):
    for a in rows(messy_conn, "SELECT * FROM account_assessments WHERE current_stage = 'Stalled'"):
        assert a["days_since_meaningful_activity"] >= 45
