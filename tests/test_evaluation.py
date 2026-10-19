"""Milestone 8: the checks against the answer key and the clean twin, as written by evaluate.py."""
from calder_abm import config
from helpers import rows


def results(conn, test=None):
    sql = "SELECT * FROM evaluation_results" + (" WHERE test = ?" if test else "")
    return rows(conn, sql, (test,) if test else ())


def test_every_check_is_recorded(messy_conn):
    tests = {r["test"] for r in results(messy_conn)}
    assert tests == {"Answer key", "Clean twin", "Planted pattern", "Backtest"}


def test_no_wrong_links_on_either_dataset(messy_conn):
    for r in results(messy_conn):
        if r["metric"] in {"high_confidence_links_wrong", "probable_links_wrong"}:
            assert r["value"] == 0, r["note"]


def test_every_planted_pattern_check_passes(messy_conn):
    planted = results(messy_conn, "Planted pattern")
    assert len(planted) == 5
    assert all(r["value"] == 1 for r in planted), [r["note"] for r in planted if r["value"] != 1]


def test_stage_mistakes_caused_by_the_mess_are_flagged(messy_conn):
    for r in results(messy_conn, "Clean twin"):
        if r["metric"] == "mess_caused_mistakes_flagged" and r["sample_size"]:
            assert r["value"] == r["sample_size"], r["note"]


def test_stages_are_mostly_right_at_every_review_point(messy_conn):
    for as_of, _ in config.review_points():
        right = [r for r in results(messy_conn, "Answer key") if r["metric"] == "stages_right" and r["as_of_date"] == as_of]
        assert right and right[0]["value"] >= 0.95 * right[0]["sample_size"]


def test_the_notes_reader_finds_facts_in_known_wording(messy_conn):
    found = [r for r in results(messy_conn, "Answer key") if r["metric"] == "note_facts_found"][0]
    assert found["value"] >= 0.9 * found["sample_size"]
