"""Milestone 4: matching records to companies and people, checked against the answer key.

The rule that matters most: an uncertain match is never forced. A record is
linked only on solid evidence; anything doubtful waits for a person.
"""
import csv
import json

from helpers import one, rows


def truth_by_record(built, name):
    with (built.answer_key / name).open(newline="", encoding="utf-8") as handle:
        return {(r["file"], int(r["source_row"])): r for r in csv.DictReader(handle)}


def decisions(conn):
    return rows(conn, "SELECT m.*, r.source_file, r.source_row FROM identity_matches m "
                      "JOIN raw_records r USING (raw_record_id)")


def test_no_high_confidence_link_points_to_the_wrong_company(built, messy_conn, clean_conn):
    for conn, name in [(messy_conn, "record_truth_messy.csv"), (clean_conn, "record_truth_clean.csv")]:
        truth = truth_by_record(built, name)
        wrong = [m for m in decisions(conn) if m["decision"] in {"Linked", "Linked as probable"}
                 and (m["source_file"], m["source_row"]) in truth
                 and truth[(m["source_file"], m["source_row"])]["account_id"] != m["matched_account_id"]]
        assert wrong == [], wrong[:3]


def test_every_decision_has_a_reason_and_only_links_carry_an_account(messy_conn):
    for m in decisions(messy_conn):
        assert m["match_reason"]
        assert (m["matched_account_id"] is not None) == (m["decision"] in {"Linked", "Linked as probable"})
        if m["decision"] == "Linked as probable":
            assert m["match_confidence"] == "Medium"


def test_a_name_shared_by_two_companies_waits_for_review(messy_conn):
    # The monthly ad report names companies without a domain or CRM ID. "Meridian Health" is both
    # ACC-002 (US) and ACC-003 (Canada). (A meeting note that carries a CRM ID is linked by that ID.)
    meridian = rows(messy_conn, "SELECT m.decision, m.candidate_account_ids FROM identity_matches m "
                                "JOIN raw_records r USING (raw_record_id) WHERE m.input_company_name = "
                                "'Meridian Health' AND r.source_file = 'linkedin_ads.csv'")
    assert meridian, "the scenario always writes Meridian Health without a domain in the ad reports"
    for m in meridian:
        assert m["decision"] == "Needs review"
        assert set(json.loads(m["candidate_account_ids"])) == {"ACC-002", "ACC-003"}


def test_a_merely_similar_name_is_not_linked(messy_conn):
    supply = rows(messy_conn, "SELECT decision FROM identity_matches WHERE input_company_name = 'ABC Supply'")
    assert supply and {m["decision"] for m in supply} == {"Needs review"}


def test_similar_companies_stay_separate(messy_conn):
    names = dict(messy_conn.execute("SELECT account_id, company_name FROM accounts").fetchall())
    assert names["ACC-005"] != names["ACC-006"]        # Summit Financial Group and Summit Financial Holdings
    assert one(messy_conn, "SELECT COUNT(*) FROM accounts") == 120


def test_personal_email_addresses_are_never_linked(messy_conn):
    personal = rows(messy_conn, "SELECT decision FROM identity_matches WHERE input_email LIKE '%@gmail.com' "
                                "AND input_company_name IS NULL AND input_domain IS NULL")
    assert personal and all(m["decision"] == "Not linked" for m in personal)


def test_the_clean_twin_links_everything_identifiable(clean_conn):
    assert one(clean_conn, "SELECT COUNT(*) FROM identity_matches WHERE decision = 'Needs review'") == 0
    assert one(clean_conn, "SELECT COUNT(*) FROM identity_matches WHERE decision = 'Linked as probable'") == 0


def test_contacts_get_buying_roles_from_titles(messy_conn):
    counsel = rows(messy_conn, "SELECT buying_role FROM contacts WHERE job_title LIKE '%General Counsel%'")
    assert counsel and {c["buying_role"] for c in counsel} == {"Legal Reviewer"}
