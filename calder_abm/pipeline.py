"""Runs every step in order, for both datasets and every review point.

    python -m calder_abm.pipeline            # messy data, clean twin, then the evaluation
    python -m calder_abm.pipeline --messy    # only the messy data (what the dashboard shows)

For each dataset it builds a fresh database:
    ingest -> clean -> identity -> notes and signals
and then, for each review point, using only records that had arrived by then:
    stages -> confidence -> next action -> attention tier -> measurement
    -> won-versus-lost comparisons and budget verdicts -> executive summary
Finally the evaluation compares both databases with the answer key.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import attribution, clean, config, db, identity, ingest, measurement, signals, summary
from . import timeline as tl
from .prioritize import attention_tier
from .recommend import Facts, recommend
from .stages import assess
from .uncertainty import confidence, known_facts, unknowns


OPEN_DEAL_STAGES = {"Qualified Lead", "Active Conversation", "Proposal / RFP", "Verbal Commitment"}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assess_review_point(conn: sqlite3.Connection, as_of: str, accounts: dict, contacts: dict,
                        opportunities: list[dict], all_signals: list[dict], open_questions: list[dict],
                        is_latest: bool) -> dict:
    """Stage, confidence, tier and next action for every account at one review point."""
    visible = tl.by_account(all_signals, as_of)
    visible_all = tl.by_account(all_signals, as_of, include_duplicates=True)
    open_opps = {}
    for o in opportunities:
        if o["status"] == "Open" or (o.get("closed_date") and o["closed_date"] > as_of):
            if o.get("created_date") and o["created_date"] <= as_of:
                open_opps.setdefault(o["account_id"], o)
    results, assessment_rows, recommendation_rows, statement_rows = {}, [], [], []
    created = now_utc()
    for account_id, account in accounts.items():
        sigs = visible.get(account_id, [])
        opp = open_opps.get(account_id)
        crm_stage = opp.get("crm_stage") if (opp and is_latest) else None
        r = assess(sigs, as_of, crm_stage=crm_stage)
        f = Facts(account, r, "", sigs, visible_all.get(account_id, []), contacts, as_of, opp)
        level, reasons, failed = confidence(r, f.roles_seen, as_of)
        f.confidence = level
        if level == "Insufficient evidence":
            r.trend, r.trend_rule = "Unclear", "TRD-UNCLEAR"
            r.trend_reason = "Confidence is Insufficient evidence, so the trend cannot be judged"
        rec = recommend(f)
        tier, tier_reason = attention_tier(f, rec["rule_id"])
        questions = [q for q in open_questions if q["account_id"] == account_id
                     and tl.visible({"occurred_at": q["occurred_at"], "received_at": q["received_at"]}, as_of)]
        unknown = unknowns(r, visible_all.get(account_id, []), f.roles_seen, opp, questions, as_of)
        if level == "Insufficient evidence":
            unknown.insert(0, {"id": "UNK-STATUS", "statement": (
                f"Did this activity really come from {account['company_name']}? Confirm before acting on it."
                if "INS-PROBABLE" in failed else "What is the real status? The sources disagree; ask the account owner.")})
        if rec["missing"]:
            unknown.append({"id": "UNK-CONTACT", "statement": rec["missing"]})
        if not unknown and r.stage in OPEN_DEAL_STAGES:
            unknown.append({"id": "UNK-OUTCOME", "statement": "Whether they will go ahead: every question on the "
                                                              "checklist is answered, but nothing yet guarantees a signature."})
        known = known_facts(r, account["company_name"], contacts, as_of)
        value = None
        if opp and opp.get("estimated_value_usd"):
            value = opp["estimated_value_usd"]
        positives = [s for s in sigs if s["sentiment"] == "Positive"]
        negatives = [s for s in sigs if s["sentiment"] == "Negative"]
        last_important = next((s for s in reversed(sigs) if s["signal_type"] not in {"ad_shown", "outreach_sent"}), None)
        results[account_id] = {"result": r, "facts": f, "confidence": level, "reasons": reasons, "tier": tier,
                               "recommendation": rec, "unknowns": unknown, "known": known}
        assessment_rows.append({
            "account_id": account_id, "as_of_date": as_of, "current_stage": r.stage, "stage_note": r.stage_note,
            "last_active_stage": r.last_active_stage, "trend": r.trend, "trend_reason": r.trend_reason,
            "confidence": level, "confidence_reasons": json.dumps(reasons), "attention_tier": tier,
            "last_important_signal_id": last_important["signal_id"] if last_important else None,
            "days_since_meaningful_activity": r.quiet_days if r.last_meaningful else None,
            "usable_signal_count": len(sigs), "open_opportunity_value_usd": value,
            "positive_summary": summarize(positives), "negative_summary": summarize(negatives)})
        rec_id = f"REC-{as_of}-{account_id}"
        recommendation_rows.append({
            "recommendation_id": rec_id, "account_id": account_id, "as_of_date": as_of, "rule_id": rec["rule_id"],
            "target_contact_id": rec["target_contact_id"], "target_role": rec["target_role"],
            "action": rec["action"], "channel": rec["channel"], "reason": rec["reason"], "confidence": level,
            "priority_rank": None, "created_at": created})
        order = 0
        for item in known:
            order += 1
            statement_rows.append({"recommendation_id": rec_id, "kind": "KNOWN", "statement": item["statement"],
                                   "evidence_refs": json.dumps(item["evidence"]), "rule_id": None, "sort_order": order})
        stage_text = r.stage + (f" ({r.stage_note})" if r.stage_note else "")
        inferred = [(f"{account['company_name']} is at {stage_text}.", r.rule_id),
                    (f"Trend: {r.trend}. {r.trend_reason}.", r.trend_rule),
                    (f"Confidence: {level}, because {'; '.join(soften(x) for x in reasons)}.",
                     "CONF-" + ("CHECKS" if level != "Insufficient evidence" else "INSUFFICIENT")),
                    (f"Attention tier: {tier}. {tier_reason}", "TIER-" + tier.upper().replace(" ", "-"))]
        for text_, rule in inferred:
            order += 1
            statement_rows.append({"recommendation_id": rec_id, "kind": "INFERRED", "statement": text_,
                                   "evidence_refs": None, "rule_id": rule, "sort_order": order})
        for item in unknown:
            order += 1
            statement_rows.append({"recommendation_id": rec_id, "kind": "UNKNOWN", "statement": item["statement"],
                                   "evidence_refs": None, "rule_id": None, "sort_order": order})

    # Priority order on the dashboard: tier, then known value, then target tier.
    tier_rank = {t: i for i, t in enumerate(["Act now", "Find out", "Push", "Nurture", "Reduce spend", "Won"])}
    ordered = sorted(assessment_rows, key=lambda a: (tier_rank[a["attention_tier"]],
                                                    -(a["open_opportunity_value_usd"] or 0),
                                                    accounts[a["account_id"]].get("target_tier") or 9))
    rank_of = {a["account_id"]: i for i, a in enumerate(ordered, start=1)}
    for rec in recommendation_rows:
        rec["priority_rank"] = rank_of[rec["account_id"]]
    db.insert(conn, "account_assessments", assessment_rows)
    db.insert(conn, "recommendations", recommendation_rows)
    db.insert(conn, "recommendation_statements", statement_rows)
    conn.commit()
    return results


def soften(reason: str) -> str:
    """A reason written as a sentence, made to fit after 'because': no capital, no full stop."""
    reason = reason.strip().rstrip(".")
    return reason[0].lower() + reason[1:] if len(reason) > 1 and reason[1].islower() else reason


def summarize(items: list[dict], limit: int = 4) -> str | None:
    if not items:
        return None
    labels = {}
    for s in items:
        labels[config.signal_label(s["signal_type"])] = labels.get(config.signal_label(s["signal_type"]), 0) + 1
    top = sorted(labels.items(), key=lambda kv: -kv[1])[:limit]
    return "; ".join(f"{label} x{n}" if n > 1 else label for label, n in top)


def save_stage_history(conn: sqlite3.Connection, results: dict) -> None:
    rows = []
    for account_id, item in results.items():
        r = item["result"]
        for h in r.history:
            rows.append({"account_id": account_id, "from_stage": h["from_stage"], "to_stage": h["to_stage"],
                         "effective_at": h["effective_at"], "learned_at": h["learned_at"], "rule_id": h["rule_id"],
                         "evidence_signal_ids": json.dumps(h["evidence_signal_ids"]),
                         "confidence": item["confidence"]})
    db.insert(conn, "stage_history", rows)
    for account_id, item in results.items():
        conn.execute("UPDATE accounts SET current_stage = ? WHERE account_id = ?", (item["result"].stage, account_id))
    conn.commit()


def run_dataset(variant: str, data_dir: Path, db_path: Path, quiet: bool = False) -> dict:
    """Build one complete database. Returns a short report."""
    started = time.time()
    conn = db.create_database(db_path)
    conn.execute("INSERT INTO pipeline_runs (dataset_variant, started_at) VALUES (?, ?)", (variant, now_utc()))
    raw = ingest.ingest_all(conn, data_dir)
    cleaner = clean.Cleaner()
    records = cleaner.clean_all(raw)
    cleaner.save(conn)
    clean.save_reference_rows(conn, records)
    resolved = identity.resolve_all(conn, records)
    built = signals.build_signals(conn, records, resolved, config.latest_review_point())
    accounts = resolved["accounts"]
    contacts = resolved["contacts"]
    opportunities = resolved["opportunities"]
    all_signals = built["signals"]
    by_point = {}
    points = config.review_points()
    for as_of, label in points:
        is_latest = as_of == points[-1][0]
        results = assess_review_point(conn, as_of, accounts, contacts, opportunities, all_signals,
                                      built["open_questions"], is_latest)
        by_point[as_of] = results
        measurement.run(conn, as_of, accounts, all_signals, results, opportunities)
        attribution.run(conn, as_of, accounts, all_signals, results, opportunities)
        summary.run(conn, as_of, label, accounts, results, opportunities)
    save_stage_history(conn, by_point[points[-1][0]])
    report = {
        "variant": variant, "raw_rows": len(raw), "accounts": len(accounts), "contacts": len(contacts),
        "signals": len(all_signals), "issues": conn.execute("SELECT COUNT(*) FROM data_quality_issues").fetchone()[0],
        "review_queue": conn.execute("SELECT COUNT(*) FROM identity_matches WHERE decision = 'Needs review'").fetchone()[0],
        "seconds": round(time.time() - started, 1)}
    conn.execute("UPDATE pipeline_runs SET finished_at = ?, status = 'Succeeded', summary = ?",
                 (now_utc(), json.dumps(report)))
    conn.commit()
    conn.close()
    if not quiet:
        latest = by_point[points[-1][0]]
        stages = {}
        for item in latest.values():
            stages[item["result"].stage] = stages.get(item["result"].stage, 0) + 1
        print(f"  {variant}: {report['raw_rows']} raw rows -> {report['signals']} signals, "
              f"{report['issues']} data quality notes, {report['review_queue']} records waiting for review "
              f"({report['seconds']}s)")
        print("    stages at the latest review point: " +
              ", ".join(f"{k} {v}" for k, v in sorted(stages.items(), key=lambda kv: -kv[1])))
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Calder Hollis databases from the raw files.")
    parser.add_argument("--messy", action="store_true", help="only the messy data (skip the clean twin and evaluation)")
    args = parser.parse_args(argv)
    raw_dir, clean_dir = config.project_path("raw_dir"), config.project_path("clean_dir")
    if not (raw_dir / "target_accounts.csv").exists():
        raise SystemExit("No data found. Run  python -m calder_abm.generate_data  first.")
    print("Building the databases...")
    run_dataset("messy", raw_dir, config.project_path("messy_db"))
    if args.messy:
        return
    run_dataset("clean", clean_dir, config.project_path("clean_db"))
    from . import evaluate
    evaluate.run(config.project_path("messy_db"), config.project_path("clean_db"),
                 config.project_path("answer_key_dir"))


if __name__ == "__main__":
    main()
