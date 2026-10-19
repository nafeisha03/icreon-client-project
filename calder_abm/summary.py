"""Answers the six management questions in cautious sentences linked to their evidence.

    What appears to be working?          What is not working?
    Which accounts deserve more attention?   Which accounts should receive less spend?
    Where should the next marketing budget go?   What can we not determine?

Marketing claims never get High confidence: without a planned comparison group
they show association, not cause. A consistent pattern is Medium at best.
"""
from __future__ import annotations

import json
import sqlite3

from . import config, db
from .text import day, join_words, money, plural

CONFIDENCE_FOR_EVIDENCE = {"Consistent pattern": "Medium", "Association": "Low", "No clear difference": "Low",
                           "Insufficient evidence": "Insufficient evidence"}


def run(conn: sqlite3.Connection, as_of: str, label: str, accounts: dict, results: dict, opportunities: list[dict]) -> None:
    goal = config.settings()["goal"]
    rows = []
    order = 0

    def add(question, statement, kind, confidence=None, refs=None):
        nonlocal order
        order += 1
        rows.append({"as_of_date": as_of, "question": question, "statement": statement, "kind": kind,
                     "confidence": confidence, "evidence_refs": json.dumps(refs) if refs else None,
                     "sort_order": order})

    names = {c["campaign_id"]: c["name"] for c in db.query(conn, "SELECT campaign_id, name FROM campaigns")}
    verdicts = db.query(conn, "SELECT * FROM budget_recommendations WHERE as_of_date = ? AND subject_type = 'campaign'",
                        (as_of,))
    signed = sorted((a for a, item in results.items() if item["result"].stage == "Signed Agreement"),
                    key=lambda a: results[a]["result"].terminal_date)
    by_goal = [a for a in signed if results[a]["result"].terminal_date.date().isoformat() <= goal["goal_date"]]
    # Only opportunity values the CRM held by the review point.
    values = {}
    for o in opportunities:
        if o.get("estimated_value_usd") and o.get("created_date") and o["created_date"] <= as_of:
            values.setdefault(o["account_id"], o["estimated_value_usd"])
    signed_value = sum(values.get(a, 0) for a in signed)

    # Progress against the goal (a known fact, not a judgment).
    refs = [results[a]["result"].key_signal["signal_id"] for a in signed if results[a]["result"].key_signal]
    if as_of >= goal["goal_date"]:
        add("working", f"{plural(len(signed), 'engagement')} signed so far, {len(by_goal)} of them by the 18-month goal "
                       f"date ({day(goal['goal_date'])}), against a goal of {goal['engagements']}. Known signed value: "
                       f"{money(signed_value)}.", "KNOWN", "High", refs)
    else:
        add("working", f"{plural(len(signed), 'engagement')} signed so far, against a goal of {goal['engagements']} by "
                       f"{day(goal['goal_date'])}. Known signed value: {money(signed_value)}.", "KNOWN", "High", refs)

    increase = [v for v in verdicts if v["verdict"] == "Increase"]
    for v in increase:
        add("working", f"{names.get(v['subject_id'], v['subject_id'])}: {v['reason']}", "INFERRED",
            CONFIDENCE_FOR_EVIDENCE[v["evidence_level"]], [v["subject_id"]])
    if not increase:
        add("working", "No program shows a consistent pattern before progress yet.", "INFERRED", "Low")
    # Promising but not proven: seen more often before progress, without passing every check.
    promising = [v for v in verdicts if v["verdict"] == "Continue" and v["evidence_level"] == "Association"
                 and "mostly after that progress" not in v["reason"]]
    for v in promising:
        add("working", f"Promising, not proven: {names.get(v['subject_id'], v['subject_id'])}. {v['reason']}",
            "INFERRED", "Low", [v["subject_id"]])

    for v in [v for v in verdicts if v["verdict"] in {"Stop", "Reduce"}]:
        add("not_working", f"{names.get(v['subject_id'], v['subject_id'])} ({v['verdict'].lower()}): {v['reason']}",
            "INFERRED", CONFIDENCE_FOR_EVIDENCE.get(v["evidence_level"], "Low"), [v["subject_id"]])
    if not any(v["verdict"] in {"Stop", "Reduce"} for v in verdicts):
        add("not_working", "No program meets the rules for reducing or stopping it yet.", "INFERRED", "Low")

    ranked = sorted(results.items(), key=lambda kv: kv[1]["facts"].account.get("account_id"))
    tiers = {}
    for a, item in ranked:
        tiers.setdefault(item["tier"], []).append(a)
    for tier in ["Act now", "Find out", "Push"]:
        members = tiers.get(tier, [])
        if not members:
            continue
        members.sort(key=lambda a: -(values.get(a) or 0))
        listed = [f"{accounts[a]['company_name']} ({results[a]['result'].stage}"
                  f"{', ' + money(values[a]) if values.get(a) else ''})" for a in members[:6]]
        more = f" and {len(members) - 6} more" if len(members) > 6 else ""
        add("more_attention", f"{tier}: {join_words(listed)}{more}.", "INFERRED",
            "Medium" if tier != "Find out" else "Insufficient evidence", members)
    reduce = tiers.get("Reduce spend", [])
    if reduce:
        stalled = [a for a in reduce if results[a]["result"].stage == "Stalled"]
        lost = [a for a in reduce if results[a]["result"].stage == "Lost"]
        cold = [a for a in reduce if a not in stalled and a not in lost]
        parts = []
        if stalled:
            parts.append(f"{plural(len(stalled), 'account')} stalled for 90 days or more")
        if lost:
            parts.append(f"{len(lost)} lost")
        if cold:
            parts.append(f"{len(cold)} reached for more than a sales cycle with no engagement in the last six months")
        add("less_spend", f"{plural(len(reduce), 'account')} should receive less spend: {join_words(parts)}.",
            "INFERRED", "Medium", reduce)
    else:
        add("less_spend", "None yet: no account has stalled for 90 days or been lost, and none has been reached for a "
                          "full sales cycle without responding.", "INFERRED", "Medium")
    cut = [v for v in verdicts if v["verdict"] in {"Stop", "Reduce"}]
    if cut:
        add("less_spend", "Programs to scale back (see 'What is not working?'): "
                          f"{join_words([names.get(v['subject_id'], v['subject_id']) for v in cut])}.",
            "INFERRED", "Low", [v["subject_id"] for v in cut])

    if increase:
        add("next_budget", f"Move budget toward {join_words([names.get(v['subject_id'], v['subject_id']) for v in increase])}, "
                           "away from the programs marked Reduce or Stop, and keep a random set of comparable target "
                           "accounts out of one program, so the next review can test cause and not just association.",
            "INFERRED", "Low")
    else:
        add("next_budget", "Keep the current mix, and keep a random set of comparable target accounts out of one "
                           "program so the next review can test cause.", "INFERRED", "Low")

    end = f"{as_of}T23:59:59Z"
    review_queue = conn.execute("SELECT COUNT(*) FROM identity_matches WHERE decision = 'Needs review' "
                                "AND (record_date IS NULL OR record_date <= ?)", (end,)).fetchone()[0]
    missed_notes = conn.execute("SELECT COUNT(*) FROM data_quality_issues i LEFT JOIN identity_matches m "
                                "USING (raw_record_id) WHERE i.issue_type = 'hedged_statement' "
                                "AND (m.record_date IS NULL OR m.record_date <= ?)", (end,)).fetchone()[0]
    add("cannot_determine", "Whether any program caused a signature: no comparable accounts were deliberately left out, "
                            "so every marketing result here is association, not proof.", "UNKNOWN")
    add("cannot_determine", f"Data-driven attribution or win predictions: {plural(len(signed), 'won deal')} so far, "
                            "far too few to learn from.", "UNKNOWN")
    add("cannot_determine", "Partner conversations, dinners and calls that nobody logged; they get no credit under any "
                            "rule, and stages that depend on them may be behind.", "UNKNOWN")
    add("cannot_determine", f"Which company {plural(review_queue, 'record')} in the review queue belong to, until a person "
                            f"settles them; plus {plural(missed_notes, 'hedged note')} that may or may not be facts.",
        "UNKNOWN")
    if label.lower().startswith("six-month"):
        add("cannot_determine", "Return on the marketing budget: at month 6 almost no deal has had time to close, so "
                                "this review can only report leading signs.", "UNKNOWN")
    db.insert(conn, "executive_summary", rows)
    conn.commit()
