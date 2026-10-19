"""Milestone 7: the 30-day and 330-day windows, the comparisons, credit rules and budget verdicts."""
import math

import pytest

from calder_abm import config
from calder_abm.attribution import counts_text, evidence_level, fisher_two_sided
from helpers import first, latest, one, rows


# -- the counting rules on their own -------------------------------------------------------------
def test_fishers_exact_test_matches_known_values():
    assert math.isclose(fisher_two_sided(3, 1, 1, 3), 0.4857, abs_tol=1e-4)
    assert math.isclose(fisher_two_sided(1, 9, 11, 3), 0.0028, abs_tol=1e-4)
    assert fisher_two_sided(5, 5, 5, 5) == 1.0


SEGMENTS_HOLD = [(5, 5, 2, 10), (17, 17, 19, 59)]


@pytest.mark.parametrize("counts, before, segments, chance, expected", [
    ((2, 4, 10, 20), None, [], False, "Insufficient evidence"),          # under 5 accounts on a side
    ((5, 10, 5, 10), None, [], False, "No clear difference"),            # under 25 points apart
    ((1, 10, 8, 10), None, [], False, "Association"),                    # seen LESS often in progress
    ((22, 22, 29, 78), (22, 22, 29, 78), SEGMENTS_HOLD, False, "Consistent pattern"),
    ((22, 22, 29, 78), (22, 22, 29, 78), SEGMENTS_HOLD, True, "Association"),        # could be chance
    ((22, 22, 29, 78), (3, 10, 48, 90), SEGMENTS_HOLD, False, "Association"),        # came after progress
    ((22, 22, 29, 78), (22, 22, 29, 78), [(0, 0, 15, 24), (11, 14, 25, 62)], False, "Association"),  # one side too small
])
def test_evidence_levels(counts, before, segments, chance, expected):
    level, _ = evidence_level(*counts, before or counts, segments, chance)
    assert level == expected


def test_comparisons_are_written_in_counts():
    text = counts_text("campaign", "reached by Executive Roundtables", "moved into the pipeline", (22, 22, 29, 78))
    assert text == ("Of 22 accounts reached by Executive Roundtables, 22 moved into the pipeline; "
                    "of the 78 not reached, 29 did.")


# -- the full build -------------------------------------------------------------------------------
def test_every_program_is_measured_in_both_windows(messy_conn):
    windows = {int(config.settings()["windows"]["short_days"]), int(config.settings()["windows"]["long_days"])}
    for as_of, _ in config.review_points():
        for campaign_id in [r[0] for r in messy_conn.execute("SELECT campaign_id FROM campaigns")]:
            found = {r[0] for r in messy_conn.execute(
                "SELECT DISTINCT window_days FROM measurements WHERE as_of_date = ? AND subject_type = 'campaign' "
                "AND subject_id = ? AND metric = 'advanced_a_stage'", (as_of, campaign_id))}
            assert found == windows


def test_the_long_window_never_counts_fewer_moves_than_the_short_one(messy_conn):
    pairs = rows(messy_conn, "SELECT s.value AS short, l.value AS long FROM measurements s JOIN measurements l "
                             "ON s.as_of_date = l.as_of_date AND s.subject_type = l.subject_type "
                             "AND s.subject_id = l.subject_id AND s.metric = l.metric "
                             "WHERE s.metric = 'advanced_a_stage' AND s.window_days = 30 AND l.window_days = 330")
    assert pairs and all(p["short"] <= p["long"] for p in pairs)


def test_marketing_claims_say_association_not_proof(messy_conn):
    claims = rows(messy_conn, "SELECT statement FROM outcome_comparisons WHERE segment = 'All accounts' "
                              "AND evidence_level IN ('Association', 'Consistent pattern')")
    assert claims and all(c["statement"].endswith("Association, not proof.") for c in claims)


def test_the_planted_real_effect_gets_an_increase_and_the_traps_do_not(messy_conn):
    verdict = dict(messy_conn.execute("SELECT subject_id, verdict FROM budget_recommendations WHERE as_of_date = ? "
                                      "AND subject_type = 'campaign'", (latest(),)).fetchall())
    assert verdict["CMP-03"] == "Increase"                       # roundtables: the planted real effect
    assert verdict["CMP-07"] in {"Reduce", "Stop"}               # LinkedIn ads that change nothing
    assert verdict["CMP-11"] == "Insufficient evidence"          # PR reaches no identifiable account
    for trap in ["CMP-01", "CMP-02", "webinar_attended", "content_downloaded"]:
        assert one(messy_conn, "SELECT COUNT(*) FROM outcome_comparisons WHERE activity = ? "
                               "AND evidence_level = 'Consistent pattern'", (trap,)) == 0


def test_six_months_is_too_early_to_increase_anything(messy_conn):
    assert one(messy_conn, "SELECT COUNT(*) FROM budget_recommendations WHERE as_of_date = ? "
                           "AND verdict = 'Increase'", (first(),)) == 0


def test_credit_rules_share_out_whole_deals(messy_conn):
    for convention in ["first_touch", "last_touch", "linear", "u_shaped"]:
        credited = rows(messy_conn, "SELECT SUM(value) AS deals, MAX(sample_size) AS won FROM measurements "
                                    "WHERE as_of_date = ? AND subject_type = 'campaign' AND metric = ?",
                        (latest(), f"credit_{convention}_deals"))[0]
        assert math.isclose(credited["deals"], credited["won"], abs_tol=0.05)


def test_the_executive_summary_answers_six_questions_without_overclaiming(messy_conn):
    for as_of, _ in config.review_points():
        questions = {r[0] for r in messy_conn.execute("SELECT DISTINCT question FROM executive_summary "
                                                      "WHERE as_of_date = ?", (as_of,))}
        assert questions >= {"working", "not_working", "more_attention", "next_budget", "cannot_determine"}
    # Only the goal tracker (a recorded fact) may carry High confidence.
    high = rows(messy_conn, "SELECT kind FROM executive_summary WHERE confidence = 'High'")
    assert all(h["kind"] == "KNOWN" for h in high)
    assert one(messy_conn, "SELECT COUNT(*) FROM executive_summary WHERE kind = 'UNKNOWN'") > 0
