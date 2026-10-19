"""Checks the system against the generator's hidden answer key and against the clean twin.

Five checks, reported even when they disappoint:

  Answer key       matching and stages against the truth; a High-confidence link to
                   the wrong company is the failure that matters most
  Clean twin       where mess made a stage wrong, did the system say it was unsure?
  Planted pattern  real effects should reach Association or better; traps should never
                   reach Consistent pattern; LinkedIn ads should get Reduce or Stop
  Backtest         did accounts ranked Act now or Push at month 6 sign more often?
  Notes reader     how many facts written in notes were found (the rest are missed)

A system that is wrong but says it was unsure is behaving as designed; one that
is wrong and confident is what these checks look for.

On synthetic data these checks test the method, not Calder Hollis's marketing:
the patterns found are the patterns the generator planted.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from . import config, db
from .text import plural

UNSURE = {"Low", "Insufficient evidence"}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def identity_check(conn: sqlite3.Connection, truth_rows: list[dict]) -> dict:
    truth = {(r["file"], int(r["source_row"])): r for r in truth_rows}
    out = {"high_right": 0, "high_wrong": 0, "probable_right": 0, "probable_wrong": 0,
           "review_with_true_account": 0, "not_linked_with_true_account": 0, "correctly_unlinked": 0}
    rows = db.query(conn, "SELECT m.decision, m.matched_account_id, r.source_file, r.source_row "
                          "FROM identity_matches m JOIN raw_records r USING (raw_record_id)")
    for m in rows:
        t = truth.get((m["source_file"], m["source_row"]))
        if not t:
            continue
        true_account = t["account_id"]
        if m["decision"] == "Linked":
            out["high_right" if m["matched_account_id"] == true_account else "high_wrong"] += 1
        elif m["decision"] == "Linked as probable":
            out["probable_right" if m["matched_account_id"] == true_account else "probable_wrong"] += 1
        elif m["decision"] == "Needs review":
            out["review_with_true_account"] += int(bool(true_account))
        elif true_account:
            out["not_linked_with_true_account"] += 1
        else:
            out["correctly_unlinked"] += 1
    return out


def mistakes_text(wrong: int, flagged: int) -> str:
    if wrong == 0:
        return "no mistakes"
    if wrong == 1:
        return "1 mistake, " + ("flagged Low or Insufficient evidence" if flagged else "NOT flagged as uncertain")
    return f"{wrong} mistakes, {flagged} of them flagged Low or Insufficient evidence"


def stage_check(conn: sqlite3.Connection, accounts_truth: dict, as_of: str) -> dict:
    rows = db.query(conn, "SELECT account_id, current_stage, confidence FROM account_assessments WHERE as_of_date = ?",
                    (as_of,))
    wrong = [r for r in rows if accounts_truth[r["account_id"]][f"stage_{as_of}"] != r["current_stage"]]
    return {"total": len(rows), "right": len(rows) - len(wrong), "wrong": wrong,
            "wrong_flagged": sum(1 for r in wrong if r["confidence"] in UNSURE)}


def run(messy_db: Path, clean_db: Path, answer_key_dir: Path, quiet: bool = False) -> list[dict]:
    answer_key_dir = Path(answer_key_dir)
    if not (answer_key_dir / "accounts_truth.csv").exists():
        print("  No answer key found; evaluation skipped.")
        return []
    accounts_truth = {r["account_id"]: r for r in read_csv(answer_key_dir / "accounts_truth.csv")}
    points = config.review_points()
    latest = points[-1][0]
    messy = db.connect(messy_db)
    clean = db.connect(clean_db)
    rows: list[dict] = []

    def add(as_of, test, metric, value, sample, note):
        rows.append({"as_of_date": as_of, "test": test, "metric": metric, "value": value,
                     "sample_size": sample, "note": note})

    # 1. Answer key: matching.
    for label, conn, truth_file, test in [("messy data", messy, "record_truth_messy.csv", "Answer key"),
                                          ("clean twin", clean, "record_truth_clean.csv", "Clean twin")]:
        ident = identity_check(conn, read_csv(answer_key_dir / truth_file))
        linked = ident["high_right"] + ident["high_wrong"]
        add(latest, test, "high_confidence_links_wrong", ident["high_wrong"], linked,
            f"{label}: {ident['high_wrong']} of {linked:,} High-confidence links point to the wrong company")
        probable = ident["probable_right"] + ident["probable_wrong"]
        add(latest, test, "probable_links_wrong", ident["probable_wrong"], probable,
            f"{label}: {ident['probable_wrong']} of {probable:,} probable links point to the wrong company")
        add(latest, test, "records_waiting_for_review", ident["review_with_true_account"], None,
            f"{label}: {ident['review_with_true_account']} records that do belong to a target account wait in the review queue")
        add(latest, test, "records_not_linked", ident["not_linked_with_true_account"], None,
            f"{label}: {ident['not_linked_with_true_account']} records that belong to a target account could not be "
            f"linked (personal emails, internet providers, missing attendees); {ident['correctly_unlinked']:,} were "
            "correctly left unlinked")

    # 2. Answer key: stages; 3. Clean twin: did the mess come with a warning?
    for as_of, label in points:
        m = stage_check(messy, accounts_truth, as_of)
        c = stage_check(clean, accounts_truth, as_of)
        add(as_of, "Answer key", "stages_right", m["right"], m["total"],
            f"{label}: {m['right']} of {m['total']} stages match the truth; "
            f"{mistakes_text(len(m['wrong']), m['wrong_flagged'])}")
        add(as_of, "Answer key", "wrong_stages_flagged", m["wrong_flagged"], len(m["wrong"]),
            "; ".join(f"{w['account_id']}: {accounts_truth[w['account_id']][f'stage_{as_of}']} shown as "
                      f"{w['current_stage']} ({w['confidence']})" for w in m["wrong"]) or "No stage mistakes")
        clean_right = {r["account_id"] for r in db.query(clean, "SELECT account_id, current_stage FROM account_assessments "
                                                                "WHERE as_of_date = ?", (as_of,))
                       if accounts_truth[r["account_id"]][f"stage_{as_of}"] == r["current_stage"]}
        caused = [w for w in m["wrong"] if w["account_id"] in clean_right]
        flagged = sum(1 for w in caused if w["confidence"] in UNSURE)
        add(as_of, "Clean twin", "stages_right_on_clean_data", c["right"], c["total"],
            f"{label}: on the clean twin {c['right']} of {c['total']} stages match the truth"
            + ("" if c["right"] == c["total"] else " (the rest depend on steps nobody recorded)"))
        add(as_of, "Clean twin", "mess_caused_mistakes_flagged", flagged, len(caused),
            f"{label}: the mess made {plural(len(caused), 'stage')} wrong that the clean twin got right; "
            f"{mistakes_text(len(caused), flagged).split(', ', 1)[1]}" if caused
            else f"{label}: the mess made no stage wrong")

    # 4. Planted patterns, at the latest review point.
    def comparison(activity: str, comparison_name: str = "Advanced vs not") -> dict | None:
        found = db.query(messy, "SELECT * FROM outcome_comparisons WHERE as_of_date = ? AND activity = ? AND "
                                "comparison = ? AND segment = 'All accounts'", (latest, activity, comparison_name))
        return found[0] if found else None

    def levels(activity: str) -> set[str]:
        return {r["evidence_level"] for r in db.query(messy, "SELECT evidence_level FROM outcome_comparisons WHERE "
                                                             "as_of_date = ? AND activity = ? AND segment = 'All accounts'",
                                                      (latest, activity))}

    for name, activity in [("Roundtables (real effect)", "CMP-03"), ("Technology partner referrals (real effect)", "CMP-06")]:
        c = comparison(activity)
        ok = bool(c) and c["evidence_level"] in {"Association", "Consistent pattern"} and \
            c["group_a_with"] / max(c["group_a_total"], 1) > c["group_b_with"] / max(c["group_b_total"], 1)
        add(latest, "Planted pattern", f"real_effect_found::{activity}", int(ok), c["group_a_total"] if c else 0,
            f"{name}: {'found' if ok else 'NOT found'} ({c['evidence_level'] if c else 'no comparison'}). "
            f"{c['statement'] if c else ''}")
    for name, activities in [("Report read after progress (trap)", ["CMP-02", "content_downloaded"]),
                             ("Current clients who buy anyway, via webinars (trap)", ["CMP-01", "webinar_attended"])]:
        hits = {a: levels(a) for a in activities}
        fell = [a for a, lv in hits.items() if "Consistent pattern" in lv]
        strength = ["Insufficient evidence", "No clear difference", "Association", "Consistent pattern"]
        strongest = {a: max(lv, key=strength.index) if lv else "no comparison" for a, lv in hits.items()}
        described = [f"{level} for the {'program' if a.startswith('CMP-') else repr(config.signal_label(a).lower()) + ' signal'}"
                     for a, level in strongest.items()]
        add(latest, "Planted pattern", f"trap_avoided::{activities[0]}", int(not fell), None,
            f"{name}: {'avoided' if not fell else 'NOT avoided'}. Strongest evidence reached: "
            f"{' and '.join(described)}" + ("; never a Consistent pattern." if not fell else "."))
    verdict = db.query(messy, "SELECT verdict, reason FROM budget_recommendations WHERE as_of_date = ? AND "
                              "subject_type = 'campaign' AND subject_id = 'CMP-07'", (latest,))
    ok = bool(verdict) and verdict[0]["verdict"] in {"Reduce", "Stop"}
    add(latest, "Planted pattern", "trap_avoided::CMP-07", int(ok), None,
        f"LinkedIn ads that change nothing (trap): verdict {verdict[0]['verdict'] if verdict else 'none'} "
        f"({'as expected' if ok else 'expected Reduce or Stop'})")

    # 5. Backtest: month-6 priorities against what happened by month 24.
    first = points[0][0]
    tiers = {r["account_id"]: r["attention_tier"] for r in db.query(
        messy, "SELECT account_id, attention_tier FROM account_assessments WHERE as_of_date = ?", (first,))}
    flagged = [a for a, t in tiers.items() if t in {"Act now", "Push"}]
    others = [a for a in tiers if a not in flagged]
    signed = lambda accounts: sum(1 for a in accounts if accounts_truth[a]["final_position"] == "Signed Agreement")
    add(first, "Backtest", "month6_priorities_signed", signed(flagged), len(flagged),
        f"Of {plural(len(flagged), 'account')} ranked Act now or Push at the six-month review, {signed(flagged)} signed "
        f"by month 24; of the other {len(others)}, {signed(others)} did.")

    # 6. Notes reader: facts found and missed.
    facts = read_csv(answer_key_dir / "note_facts.csv")
    quotes = {(r["account_id"], r["signal_type"]): set() for r in []}
    for s in db.query(messy, "SELECT account_id, signal_type, evidence_quote FROM signals WHERE extraction_method = 'Phrase rule'"):
        quotes.setdefault((s["account_id"], s["signal_type"]), set()).add((s["evidence_quote"] or "").strip())
    real = [f for f in facts if f["fact"] != "hedge"]
    known = [f for f in real if f["known_phrasing"] == "1"]
    other = [f for f in real if f["known_phrasing"] != "1"]
    found = lambda items: sum(1 for f in items if any(f["sentence"].rstrip(".") in q or q.rstrip(".;") in f["sentence"]
                                                     for q in quotes.get((f["account_id"], f["fact"]), set()) if q))
    add(latest, "Answer key", "note_facts_found", found(known), len(known),
        f"Notes reader: found {found(known)} of {len(known)} facts written in known phrases, and {found(other)} of "
        f"{len(other)} written in other words (those are missed by design and need the AI reader in Phase 3). "
        "Facts in notes that could not be tied to a company are not counted as found.")

    for conn in (messy,):
        conn.execute("DELETE FROM evaluation_results")
        db.insert(conn, "evaluation_results", rows)
        conn.commit()
    messy.close()
    clean.close()
    if not quiet:
        print("Evaluation against the answer key:")
        for r in rows:
            if r["metric"] in {"wrong_stages_flagged"}:
                continue
            print(f"  [{r['test']}] {r['note']}")
    return rows
