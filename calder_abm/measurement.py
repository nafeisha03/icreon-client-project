"""Builds the 30-day and 330-day views, the six-month leading signs and the stage history counts.

Every campaign and account is measured twice: 30 days after exposure (how most
web analytics works) and 330 days after (about one full sales cycle). The point
is to show how differently the same activity looks in the two windows.

Everything is in counts, with the number of accounts behind it. Nothing here
says marketing caused anything: "advanced after exposure" is what was observed.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import timedelta

from . import config, db
from . import timeline as tl
from .clean import to_datetime
from .signals import is_meaningful
from .stages import LADDER, rank

DELIBERATE_RANK = rank("Interested")


def exposures(signals_by_account: dict, campaigns: dict) -> tuple[dict, dict, dict]:
    """First exposure per account, for each campaign, each channel and any program."""
    by_campaign: dict[str, dict] = defaultdict(dict)
    by_channel: dict[str, dict] = defaultdict(dict)
    anywhere: dict[str, object] = {}
    for account_id, items in signals_by_account.items():
        for s in items:
            campaign_id = s.get("campaign_id")
            if not campaign_id or campaign_id not in campaigns:
                continue
            when = to_datetime(s["occurred_at"])
            channel = campaigns[campaign_id]["channel"]
            if account_id not in by_campaign[campaign_id] or when < by_campaign[campaign_id][account_id]:
                by_campaign[campaign_id][account_id] = when
            if account_id not in by_channel[channel] or when < by_channel[channel][account_id]:
                by_channel[channel][account_id] = when
            if account_id not in anywhere or when < anywhere[account_id]:
                anywhere[account_id] = when
    return by_campaign, by_channel, anywhere


def is_deliberate(signal_type: str) -> bool:
    info = config.signal_types()[signal_type]
    supports = info.get("supports_stage")
    return info["direction"] == "Positive" and info["category"] == "Engagement" and (
        info.get("meeting_held") or (supports in LADDER and rank(supports) >= DELIBERATE_RANK))


def window_stats(reached: dict, window: int, as_of: str, results: dict, signals_by_account: dict,
                 values: dict) -> dict:
    end = to_datetime(f"{as_of}T23:59:59Z")
    stats = {"accounts_reached": len(reached), "engaged_after_exposure": 0, "advanced_a_stage": 0,
             "reached_active_conversation_or_later": 0, "reached_proposal_or_later": 0,
             "opportunity_value_at_advancing_usd": 0, "advancing_with_unknown_value": 0,
             "windows_not_yet_complete": 0}
    days_to_next = []
    for account_id, start in reached.items():
        r = results[account_id]["result"]
        stop = min(start + timedelta(days=window), end)
        if start + timedelta(days=window) > end:
            stats["windows_not_yet_complete"] += 1
        inside = [s for s in signals_by_account.get(account_id, [])
                  if start < to_datetime(s["occurred_at"]) <= stop]
        if any(is_deliberate(s["signal_type"]) for s in inside):
            stats["engaged_after_exposure"] += 1
        before, after = r.stage_at(start), r.stage_at(stop)
        if after > before:
            stats["advanced_a_stage"] += 1
            if values.get(account_id):
                stats["opportunity_value_at_advancing_usd"] += values[account_id]
            else:
                stats["advancing_with_unknown_value"] += 1
        if before < rank("Active Conversation") <= after:
            stats["reached_active_conversation_or_later"] += 1
        if before < rank("Proposal / RFP") <= after:
            stats["reached_proposal_or_later"] += 1
        meaningful = [to_datetime(s["occurred_at"]) for s in inside if is_meaningful(s["signal_type"])]
        if meaningful:
            days_to_next.append((min(meaningful) - start).days)
    stats["median_days_to_next_meaningful_event"] = statistics.median(days_to_next) if days_to_next else None
    stats["_days_sample"] = len(days_to_next)
    return stats


def advanced_since(reached: dict, results: dict, as_of: str) -> int:
    end = to_datetime(f"{as_of}T23:59:59Z")
    return sum(1 for a, start in reached.items() if results[a]["result"].stage_at(end) > results[a]["result"].stage_at(start))


def run(conn: sqlite3.Connection, as_of: str, accounts: dict, all_signals: list[dict], results: dict,
        opportunities: list[dict]) -> None:
    settings = config.settings()
    windows = [int(settings["windows"]["short_days"]), int(settings["windows"]["long_days"])]
    campaigns = {c["campaign_id"]: c for c in db.query(conn, "SELECT * FROM campaigns")}
    visible = tl.by_account(all_signals, as_of)
    values = {}
    for o in opportunities:
        if o.get("estimated_value_usd") and o.get("created_date") and o["created_date"] <= as_of:
            values.setdefault(o["account_id"], o["estimated_value_usd"])
    by_campaign, by_channel, anywhere = exposures(visible, campaigns)
    month = as_of[:7]
    spend = defaultdict(int)
    for row in db.query(conn, "SELECT campaign_id, amount_usd FROM marketing_spend WHERE month <= ?", (month,)):
        spend[row["campaign_id"]] += row["amount_usd"]
    channel_spend = defaultdict(int)
    for campaign_id, amount in spend.items():
        if campaign_id in campaigns:
            channel_spend[campaigns[campaign_id]["channel"]] += amount

    rows = []

    def add(subject_type, subject_id, window, metric, value, sample=None, note=None):
        rows.append({"as_of_date": as_of, "subject_type": subject_type, "subject_id": subject_id,
                     "window_days": window, "metric": metric, "value": value, "sample_size": sample, "note": note})

    groups = [("campaign", cid, by_campaign.get(cid, {})) for cid in campaigns]
    groups += [("channel", ch, by_channel.get(ch, {})) for ch in sorted({c["channel"] for c in campaigns.values()})]
    groups += [("portfolio", "all", anywhere)]
    for subject_type, subject_id, reached in groups:
        for window in windows:
            stats = window_stats(reached, window, as_of, results, visible, values)
            sample = stats.pop("_days_sample")
            for metric, value in stats.items():
                note = None
                if metric == "median_days_to_next_meaningful_event":
                    note = f"based on {sample} accounts with a meaningful event in the window"
                add(subject_type, subject_id, window, metric, value, len(reached), note)
        advanced = advanced_since(reached, results, as_of)
        add(subject_type, subject_id, None, "advanced_since_first_exposure", advanced, len(reached))
        program_spend = (spend.get(subject_id, 0) if subject_type == "campaign" else
                         channel_spend.get(subject_id, 0) if subject_type == "channel" else sum(spend.values()))
        add(subject_type, subject_id, None, "spend_to_date_usd", program_spend, None)
        add(subject_type, subject_id, None, "cost_per_advancing_account_usd",
            round(program_spend / advanced) if advanced else None, advanced,
            None if advanced else "No reached account has advanced yet")
    add("portfolio", "all", None, "unassigned_spend_usd", spend.get(None, 0), None,
        "Invoices not assigned to any campaign")

    # Six-month leading signs and goal tracking (portfolio level).
    goal = settings["goal"]
    stage_now = {a: results[a]["result"] for a in accounts}
    engaged = [a for a, r in stage_now.items() if r.stage != "Target"]
    qualified = [a for a, r in stage_now.items() if max(rank(r.active_stage), r.stage_at(to_datetime(f"{as_of}T23:59:59Z"))) >= rank("Qualified Lead")]
    people_per_account = []
    for a in engaged:
        people = {s["contact_id"] for s in visible.get(a, []) if s.get("contact_id")
                  and config.signal_types()[s["signal_type"]]["category"] == "Engagement"}
        if people:
            people_per_account.append(len(people))
    first_meeting_gaps = []
    for a in engaged:
        items = visible.get(a, [])
        touches = [to_datetime(s["occurred_at"]) for s in items
                   if config.signal_types()[s["signal_type"]]["category"] == "Engagement"]
        meetings = [to_datetime(s["occurred_at"]) for s in items if config.signal_types()[s["signal_type"]].get("meeting_held")]
        if touches and meetings:
            first_meeting_gaps.append((min(meetings) - min(touches)).days)
    signed = [a for a, r in stage_now.items() if r.stage == "Signed Agreement"]
    lost = [a for a, r in stage_now.items() if r.stage == "Lost"]
    by_goal = [a for a in signed if stage_now[a].terminal_date.date().isoformat() <= goal["goal_date"]]
    add("portfolio", "all", None, "target_accounts_reached", len(anywhere), len(accounts))
    add("portfolio", "all", None, "accounts_engaged", len(engaged), len(accounts))
    add("portfolio", "all", None, "accounts_qualified_or_later", len(qualified), len(accounts))
    add("portfolio", "all", None, "median_people_engaged_per_engaged_account",
        statistics.median(people_per_account) if people_per_account else None, len(people_per_account))
    add("portfolio", "all", None, "median_days_first_touch_to_first_meeting",
        statistics.median(first_meeting_gaps) if first_meeting_gaps else None, len(first_meeting_gaps))
    add("portfolio", "all", None, "signed_to_date", len(signed), len(accounts))
    add("portfolio", "all", None, "lost_to_date", len(lost), len(accounts))
    add("portfolio", "all", None, "signed_by_goal_date", len(by_goal), None,
        f"Goal: {goal['engagements']} engagements by {goal['goal_date']}")
    add("portfolio", "all", None, "signed_value_usd", sum(values.get(a, 0) for a in signed), len(signed))

    # Known pipeline value by stage (open deals only), and how many values are unknown.
    for stage in ["Qualified Lead", "Active Conversation", "Proposal / RFP", "Verbal Commitment", "Stalled"]:
        members = [a for a, r in stage_now.items() if r.stage == stage]
        known = [values[a] for a in members if values.get(a)]
        add("portfolio", "all", None, f"pipeline_value_usd::{stage}", sum(known), len(members),
            f"{len(members) - len(known)} with no known value" if len(known) < len(members) else None)

    # History instead of percentages: how often each stage went on to sign.
    for stage in config.pipeline_stages():
        reached_stage = [a for a, r in stage_now.items() if stage in r.reached]
        add("portfolio", "all", None, f"stage_reached::{stage}", len(reached_stage), len(reached_stage))
        add("portfolio", "all", None, f"stage_signed::{stage}",
            sum(1 for a in reached_stage if stage_now[a].stage == "Signed Agreement"), len(reached_stage))
        add("portfolio", "all", None, f"stage_lost::{stage}",
            sum(1 for a in reached_stage if stage_now[a].stage == "Lost"), len(reached_stage))

    # The same account in a short and a long window.
    end = to_datetime(f"{as_of}T23:59:59Z")
    for account_id in accounts:
        r = stage_now[account_id]
        items = visible.get(account_id, [])
        if not items:
            continue
        for window in windows:
            start = end - timedelta(days=window)
            inside = [s for s in items if to_datetime(s["occurred_at"]) > start]
            add("account", account_id, window, "meaningful_signals",
                sum(1 for s in inside if is_meaningful(s["signal_type"])), None)
            add("account", account_id, window, "stage_moves",
                sum(1 for h in r.history if h["rule_id"] not in {"STG-STALLED", "STG-RESUMED"}
                    and to_datetime(h["effective_at"]) > start), None)
            add("account", account_id, window, "deliberate_steps",
                sum(1 for s in inside if is_deliberate(s["signal_type"])), None)

    db.insert(conn, "measurements", rows)
    conn.commit()
