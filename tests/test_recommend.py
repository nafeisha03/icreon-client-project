"""Milestone 6: next best actions, confidence and the KNOWN / INFERRED / UNKNOWN statements."""
import json
import re

from calder_abm import config
from helpers import first, latest, one, rows

CHANNELS = {"Email", "Partner outreach", "Meeting", "Event or webinar", "LinkedIn", "Internal"}
LEVELS = {"High", "Medium", "Low", "Insufficient evidence"}


def test_every_account_gets_one_complete_recommendation_per_review_point(messy_conn):
    for as_of, _ in config.review_points():
        recs = rows(messy_conn, "SELECT * FROM recommendations WHERE as_of_date = ?", (as_of,))
        assert len(recs) == 120 == len({r["account_id"] for r in recs})
        for r in recs:
            assert r["action"] and r["reason"] and r["target_role"] and r["rule_id"]
            assert r["channel"] in CHANNELS
            assert r["confidence"] in LEVELS
        assert sorted(r["priority_rank"] for r in recs) == list(range(1, 121))


def test_at_least_three_channels_are_used(messy_conn):
    channels = {r[0] for r in messy_conn.execute("SELECT DISTINCT channel FROM recommendations WHERE as_of_date = ?",
                                                 (latest(),))}
    assert len(channels) >= 3


def test_every_rule_in_use_is_written_in_the_playbook(messy_conn):
    written = {rule["id"] for rule in config.playbook()["playbook"]}
    used = {r[0] for r in messy_conn.execute("SELECT DISTINCT rule_id FROM recommendations")}
    assert used <= written


def test_abc_corp_gets_the_legal_walkthrough(messy_conn):
    r = rows(messy_conn, "SELECT * FROM recommendations WHERE account_id = 'ACC-001' AND as_of_date = ?", (latest(),))[0]
    assert r["rule_id"] == "A5" and r["channel"] == "Partner outreach"
    assert "General Counsel" in r["target_role"]
    assert re.search(r"[A-Z][a-z]{2} \d{1,2}, 20\d\d", r["reason"])        # the reason quotes a dated fact


def test_statements_carry_labels_and_evidence(messy_conn):
    statements = rows(messy_conn, "SELECT s.*, r.account_id FROM recommendation_statements s "
                                  "JOIN recommendations r USING (recommendation_id) WHERE r.as_of_date = ?", (latest(),))
    assert {s["kind"] for s in statements} == {"KNOWN", "INFERRED", "UNKNOWN"}
    for s in statements:
        if s["kind"] == "KNOWN":
            assert json.loads(s["evidence_refs"])               # a known fact names its signals
        if s["kind"] == "INFERRED":
            assert s["rule_id"]                                 # an inference names its rule


def test_open_deals_always_list_what_we_do_not_know(messy_conn):
    open_deals = rows(messy_conn, "SELECT r.recommendation_id FROM recommendations r JOIN account_assessments a "
                                  "ON a.account_id = r.account_id AND a.as_of_date = r.as_of_date WHERE r.as_of_date = ? "
                                  "AND a.current_stage IN ('Qualified Lead', 'Active Conversation', 'Proposal / RFP', "
                                  "'Verbal Commitment')", (latest(),))
    assert open_deals
    for d in open_deals:
        assert one(messy_conn, "SELECT COUNT(*) FROM recommendation_statements WHERE recommendation_id = ? "
                               "AND kind = 'UNKNOWN'", (d["recommendation_id"],)) > 0


def test_insufficient_evidence_means_find_out_first(messy_conn):
    unsure = rows(messy_conn, "SELECT a.*, r.rule_id FROM account_assessments a JOIN recommendations r "
                              "ON a.account_id = r.account_id AND a.as_of_date = r.as_of_date "
                              "WHERE a.confidence = 'Insufficient evidence'")
    assert unsure
    for a in unsure:
        assert a["attention_tier"] == "Find out" and a["rule_id"] == "A3" and a["trend"] == "Unclear"


def test_confidence_reasons_are_always_given(messy_conn):
    for a in rows(messy_conn, "SELECT confidence, confidence_reasons FROM account_assessments"):
        assert json.loads(a["confidence_reasons"])


def test_no_account_is_written_off_for_silence_before_one_sales_cycle(messy_conn):
    # At the six-month review nobody has been reached for a full sales cycle yet.
    assert one(messy_conn, "SELECT COUNT(*) FROM account_assessments WHERE as_of_date = ? "
                           "AND attention_tier = 'Reduce spend' AND current_stage IN ('Target', 'Aware')",
               (first(),)) == 0
