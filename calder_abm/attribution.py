"""Won-versus-lost comparisons, five credit conventions, and a budget verdict for every program.

Every comparison asks the same plain question in counts: of the accounts that had
an activity, how many progressed, and of those that did not, how many progressed?
Five rules keep the answers honest (see config/budget_rules.yaml):

  1. Like with like: "Advanced vs not" compares accounts that reached Aware or
     later; "Signed vs lost" compares closed accounts that reached Proposal / RFP.
  2. Counts, not percentages: "19 of 22 ... ; 33 of 78 ...".
  3. Timing: a pattern is consistent only if the activity came before the progress.
  4. Current clients are split out, because they buy more often anyway.
  5. Chance check: Fisher's exact test flags differences chance could easily produce.

None of this proves that marketing caused a sale. Proof needs a comparison group
that was deliberately not exposed, which this data does not have.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import defaultdict

from . import config, db
from . import timeline as tl
from .clean import to_datetime
from .stages import rank
from .text import money, plural

CURRENT = "Current client, other services"
OUR_ACTIONS = {"outreach_sent", "ad_shown"}
ACTIVITY_SIGNALS = ["event_attended", "tech_partner_referral", "webinar_attended", "content_downloaded",
                    "conference_conversation", "personal_introduction", "informal_conversation",
                    "email_replied", "linkedin_engagement", "webinar_registered", "ad_shown"]


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test for the 2x2 table [[a, b], [c, d]]."""
    row1, row2, col1, n = a + b, c + d, a + c, a + b + c + d

    def p(x):
        return math.comb(row1, x) * math.comb(row2, col1 - x) / math.comb(n, col1)

    observed = p(a)
    low, high = max(0, col1 - row2), min(row1, col1)
    return min(1.0, sum(p(x) for x in range(low, high + 1) if p(x) <= observed * (1 + 1e-9)))


def rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def counts_text(kind: str, label: str, outcome: str, counts: tuple[int, int, int, int]) -> str:
    """'Of 22 accounts reached by X, 22 moved into the pipeline; of the 78 not reached, 29 did.'"""
    a, at, b, bt = counts
    others = "without one" if kind == "signal type" else "not reached"
    return f"Of {plural(at, 'account')} {label}, {a} {outcome}; of the {bt} {others}, {b} did."


def evidence_level(with_prog, with_total, without_prog, without_total, before, segments,
                   chance: bool = False) -> tuple[str, int | None]:
    """(level, came_before_progress) from the counts."""
    rules = config.budget_rules()["comparisons"]
    minimum, gap = int(rules["min_accounts_per_side"]), float(rules["min_difference_pct_points"]) / 100
    if with_total < minimum or without_total < minimum:
        return "Insufficient evidence", None
    difference = rate(with_prog, with_total) - rate(without_prog, without_total)
    if abs(difference) < gap:
        return "No clear difference", None
    if difference < 0:
        return "Association", None
    b_with, b_with_total, b_without, b_without_total = before
    came_before = int(b_with_total >= minimum and b_without_total >= minimum
                      and rate(b_with, b_with_total) - rate(b_without, b_without_total) >= gap)
    checkable = [s for s in segments if s[1] >= minimum and s[3] >= minimum]
    holds = len(checkable) == len(segments) == 2 and all(rate(s[0], s[1]) - rate(s[2], s[3]) >= gap for s in checkable)
    if came_before and holds and not chance:
        return "Consistent pattern", 1
    return "Association", came_before


class Comparer:
    def __init__(self, as_of: str, accounts: dict, visible: dict, results: dict, campaigns: dict):
        self.as_of, self.accounts, self.visible, self.results, self.campaigns = as_of, accounts, visible, results, campaigns
        self.end = to_datetime(f"{as_of}T23:59:59Z")
        self.progress_date = {}
        for a, item in results.items():
            r = item["result"]
            dates = [d for stage, d in r.reached.items() if rank(stage) >= rank("Qualified Lead")]
            self.progress_date[a] = min(dates) if dates else None

    def activity_dates(self, kind: str, key: str) -> dict[str, list]:
        out = defaultdict(list)
        for a, items in self.visible.items():
            for s in items:
                cid = s.get("campaign_id")
                if kind == "campaign" and cid == key:
                    out[a].append(to_datetime(s["occurred_at"]))
                elif kind == "channel" and cid in self.campaigns and self.campaigns[cid]["channel"] == key:
                    out[a].append(to_datetime(s["occurred_at"]))
                elif kind == "signal type" and s["signal_type"] == key:
                    out[a].append(to_datetime(s["occurred_at"]))
        return out

    def segment(self, account_id: str) -> str | None:
        """Current clients, or new relationships (prospects and former clients). Accounts whose
        relationship is not recorded are left out of the split rather than guessed."""
        value = self.accounts[account_id].get("existing_relationship")
        if value == CURRENT:
            return "Current clients"
        if value in {"None", "Former client"}:
            return "New relationships"
        return None

    def counts(self, population: list[str], progressed: set[str], exposed: set[str]) -> tuple[int, int, int, int]:
        with_ = [a for a in population if a in exposed]
        without = [a for a in population if a not in exposed]
        return (sum(1 for a in with_ if a in progressed), len(with_),
                sum(1 for a in without if a in progressed), len(without))

    def compare(self, kind: str, key: str, label: str) -> list[dict]:
        dates = self.activity_dates(kind, key)
        rows = []
        # Advanced vs not: among accounts that reached Aware or later.
        population = [a for a, item in self.results.items() if item["result"].stage != "Target"]
        progressed = {a for a in population if self.progress_date[a]}
        exposed_any = {a for a, d in dates.items() if d}
        exposed_before = {a for a, d in dates.items()
                          if any(x <= (self.progress_date[a] or self.end) for x in d)}
        rows += self.rows(kind, key, label, "Advanced vs not", "accounts that reached Aware or later",
                          "moved into the pipeline (Qualified Lead or later)", population, progressed,
                          exposed_any, exposed_before)
        # Signed vs lost: among closed accounts that reached Proposal / RFP.
        closed = [a for a, item in self.results.items() if item["result"].stage in {"Signed Agreement", "Lost"}
                  and any(rank(s) >= rank("Proposal / RFP") for s in item["result"].reached)]
        signed = {a for a in closed if self.results[a]["result"].stage == "Signed Agreement"}
        exposed_any = {a for a in closed if any(x <= self.results[a]["result"].terminal_date for x in dates.get(a, []))}
        rows += self.rows(kind, key, label, "Signed vs lost", "closed accounts that reached Proposal / RFP",
                          "signed", closed, signed, exposed_any, exposed_any)
        return rows

    def rows(self, kind, key, label, comparison, among, outcome, population, progressed, exposed_any,
             exposed_before) -> list[dict]:
        all_counts = self.counts(population, progressed, exposed_any)
        before = self.counts(population, progressed, exposed_before)
        segments = []
        for name in ["Current clients", "New relationships"]:
            members = [a for a in population if self.segment(a) == name]
            segments.append(self.counts(members, progressed, exposed_any))
        unrecorded = sum(1 for a in population if self.segment(a) is None)
        a, at, b, bt = all_counts
        chance_p = float(config.budget_rules()["comparisons"]["chance_p_value"])
        p_value = fisher_two_sided(a, at - a, b, bt - b) if at and bt else 1.0
        chance = int(p_value > chance_p) if at and bt else None
        level, came_before = evidence_level(*all_counts, before, segments, bool(chance))
        statement = self.statement(kind, label, outcome, all_counts, before, segments, level, came_before, chance,
                                   comparison)
        out = [{"as_of_date": self.as_of, "activity": key, "activity_kind": kind, "comparison": comparison,
                "among": among, "segment": "All accounts", "group_a_with": a, "group_a_total": at,
                "group_b_with": b, "group_b_total": bt, "came_before_progress": came_before,
                "could_be_chance": chance, "evidence_level": level, "statement": statement}]
        for name, (sa, sat, sb, sbt) in zip(["Current clients", "New relationships"], segments):
            seg_level, _ = evidence_level(sa, sat, sb, sbt, (sa, sat, sb, sbt), [])
            seg_level = "Association" if seg_level == "Consistent pattern" else seg_level
            note = (f" {plural(unrecorded, 'account')} with no recorded relationship are left out of this split."
                    if unrecorded and name == "New relationships" else "")
            out.append({"as_of_date": self.as_of, "activity": key, "activity_kind": kind, "comparison": comparison,
                        "among": among, "segment": name, "group_a_with": sa, "group_a_total": sat,
                        "group_b_with": sb, "group_b_total": sbt, "came_before_progress": None,
                        "could_be_chance": int(fisher_two_sided(sa, sat - sa, sb, sbt - sb) > chance_p) if sat and sbt else None,
                        "evidence_level": seg_level,
                        "statement": f"{name}: {counts_text(kind, label, outcome, (sa, sat, sb, sbt))}{note}"})
        return out

    @staticmethod
    def statement(kind, label, outcome, counts, before, segments, level, came_before, chance, comparison) -> str:
        a, at, b, bt = counts
        text = counts_text(kind, label, outcome, counts)
        if level == "Insufficient evidence":
            return text + " Too few accounts to compare."
        if level == "No clear difference":
            return text + " No clear difference between the groups."
        if rate(a, at) < rate(b, bt):
            text += " Seen less often in accounts that progressed."
        elif level == "Consistent pattern":
            text += " Consistently seen before progress, for current clients and new relationships alike."
        else:
            text += " Seen more often in accounts that progressed."
            if comparison == "Advanced vs not" and came_before == 0:
                ba, bat, bb, bbt = before
                text += (f" Counting only activity before the progress, the difference disappears "
                         f"({ba} of {bat} against {bb} of {bbt}).")
            elif chance:
                pass
            elif segments and not all(s[1] >= 5 and s[3] >= 5 for s in segments):
                text += " Too few current clients or new relationships to check both separately."
            elif segments:
                text += " The difference does not hold within current clients and new relationships separately."
        if chance:
            text += " Could be chance."
        return text + " Association, not proof."


def credit(won: list[str], visible: dict, results: dict, values: dict, campaigns: dict) -> dict:
    """Five ways to split each won deal among the touches before it."""
    conventions = [c["id"] for c in config.budget_rules()["credit_conventions"]]
    totals = {conv: defaultdict(lambda: {"usd": 0.0, "deals": 0.0}) for conv in conventions}
    no_touch = 0
    for a in won:
        r = results[a]["result"]
        signed_at = r.terminal_date
        touches = [s for s in visible.get(a, []) if s.get("campaign_id") in campaigns
                   and s["signal_type"] not in OUR_ACTIONS and to_datetime(s["occurred_at"]) <= signed_at
                   and config.signal_types()[s["signal_type"]]["category"] == "Engagement"]
        if not touches:
            no_touch += 1
            continue
        value = values.get(a) or 0
        meetings = [to_datetime(s["occurred_at"]) for s in visible.get(a, [])
                    if config.signal_types()[s["signal_type"]].get("meeting_held")]
        first_meeting = min(meetings) if meetings else signed_at
        before_meeting = [t for t in touches if to_datetime(t["occurred_at"]) <= first_meeting] or touches
        shares = {
            "first_touch": {touches[0]["campaign_id"]: 1.0},
            "last_touch": {touches[-1]["campaign_id"]: 1.0},
            "last_touch_30": ({touches[-1]["campaign_id"]: 1.0}
                              if (signed_at - to_datetime(touches[-1]["occurred_at"])).days <= 30 else {}),
            "linear": defaultdict(float),
            "u_shaped": defaultdict(float),
        }
        for t in touches:
            shares["linear"][t["campaign_id"]] += 1 / len(touches)
        first, pivot = touches[0], before_meeting[-1]
        rest = [t for t in touches if t is not first and t is not pivot]
        if len(touches) == 1:
            shares["u_shaped"][first["campaign_id"]] += 1.0
        else:
            ends = [first, pivot] if pivot is not first else [first]
            end_share = 0.8 if rest else 1.0
            for t in ends:
                shares["u_shaped"][t["campaign_id"]] += end_share / len(ends)
            for t in rest:
                shares["u_shaped"][t["campaign_id"]] += 0.2 / len(rest)
        for conv in conventions:
            for campaign_id, share in shares[conv].items():
                totals[conv][campaign_id]["usd"] += share * value
                totals[conv][campaign_id]["deals"] += share
    return {"totals": totals, "no_touch": no_touch, "with_touch": len(won) - no_touch}


def run(conn: sqlite3.Connection, as_of: str, accounts: dict, all_signals: list[dict], results: dict,
        opportunities: list[dict]) -> None:
    rules = config.budget_rules()
    campaigns = {c["campaign_id"]: c for c in db.query(conn, "SELECT * FROM campaigns")}
    visible = tl.by_account(all_signals, as_of)
    comparer = Comparer(as_of, accounts, visible, results, campaigns)
    comparisons = []
    for cid, c in campaigns.items():
        comparisons += comparer.compare("campaign", cid, f"reached by {c['name']}")
    for channel in sorted({c["channel"] for c in campaigns.values()}):
        comparisons += comparer.compare("channel", channel, f"reached through {channel}")
    for signal_type in ACTIVITY_SIGNALS:
        comparisons += comparer.compare("signal type", signal_type,
                                        f"with a '{config.signal_label(signal_type).lower()}' signal")
    db.insert(conn, "outcome_comparisons", comparisons)

    # Credit conventions for the deals won so far.
    values = {}
    for o in opportunities:
        if o.get("estimated_value_usd") and o.get("created_date") and o["created_date"] <= as_of:
            values.setdefault(o["account_id"], o["estimated_value_usd"])
    won = [a for a, item in results.items() if item["result"].stage == "Signed Agreement"]
    split = credit(won, visible, results, values, campaigns)
    rows = []
    for conv, per_campaign in split["totals"].items():
        by_channel = defaultdict(lambda: {"usd": 0.0, "deals": 0.0})
        for cid, v in per_campaign.items():
            rows.append({"as_of_date": as_of, "subject_type": "campaign", "subject_id": cid, "window_days": None,
                         "metric": f"credit_{conv}_usd", "value": round(v["usd"]), "sample_size": split["with_touch"],
                         "note": None})
            rows.append({"as_of_date": as_of, "subject_type": "campaign", "subject_id": cid, "window_days": None,
                         "metric": f"credit_{conv}_deals", "value": round(v["deals"], 2),
                         "sample_size": split["with_touch"], "note": None})
            by_channel[campaigns[cid]["channel"]]["usd"] += v["usd"]
            by_channel[campaigns[cid]["channel"]]["deals"] += v["deals"]
        for channel, v in by_channel.items():
            rows.append({"as_of_date": as_of, "subject_type": "channel", "subject_id": channel, "window_days": None,
                         "metric": f"credit_{conv}_usd", "value": round(v["usd"]), "sample_size": split["with_touch"],
                         "note": None})
            rows.append({"as_of_date": as_of, "subject_type": "channel", "subject_id": channel, "window_days": None,
                         "metric": f"credit_{conv}_deals", "value": round(v["deals"], 2),
                         "sample_size": split["with_touch"], "note": None})
    leaders = {}
    for conv, per_campaign in split["totals"].items():
        by_channel = defaultdict(float)
        for cid, v in per_campaign.items():
            by_channel[campaigns[cid]["channel"]] += v["deals"]
        if by_channel:
            leaders[conv] = max(by_channel.items(), key=lambda kv: kv[1])[0]
    labels = {c["id"]: c["label"] for c in rules["credit_conventions"]}
    rows.append({"as_of_date": as_of, "subject_type": "portfolio", "subject_id": "all", "window_days": None,
                 "metric": "credit_leader_changes", "value": int(len(set(leaders.values())) > 1),
                 "sample_size": split["with_touch"],
                 "note": "; ".join(f"{labels[c]}: {leaders[c]}" for c in leaders) or "No won deals yet"})
    rows.append({"as_of_date": as_of, "subject_type": "portfolio", "subject_id": "all", "window_days": None,
                 "metric": "won_deals_without_recorded_touches", "value": split["no_touch"], "sample_size": len(won),
                 "note": "Won deals with no recorded marketing touch get no credit under any convention"})
    db.insert(conn, "measurements", rows)

    # Budget verdicts.
    verdicts = budget_verdicts(conn, as_of, comparer, comparisons, campaigns, results, visible)
    db.insert(conn, "budget_recommendations", verdicts)
    conn.commit()


def budget_verdicts(conn, as_of, comparer: Comparer, comparisons, campaigns, results, visible) -> list[dict]:
    settings = config.budget_rules()["verdict_settings"]
    comparison_rules = config.budget_rules()["comparisons"]
    metrics = {}
    for row in db.query(conn, "SELECT subject_type, subject_id, metric, value, sample_size FROM measurements "
                              "WHERE as_of_date = ? AND window_days IS NULL", (as_of,)):
        metrics[(row["subject_type"], row["subject_id"], row["metric"])] = row
    lookup = {(c["activity_kind"], c["activity"]): c for c in comparisons
              if c["comparison"] == "Advanced vs not" and c["segment"] == "All accounts"}
    programs = [("campaign", cid, campaigns[cid]["name"]) for cid in campaigns]
    programs += [("channel", ch, ch) for ch in sorted({c["channel"] for c in campaigns.values()})]
    end = comparer.end

    facts = {}
    for kind, key, label in programs:
        dates = comparer.activity_dates(kind, key)
        reached = {a: min(d) for a, d in dates.items() if d}
        advanced = sum(1 for a, start in reached.items() if results[a]["result"].stage_at(end) > results[a]["result"].stage_at(start))
        spend_row = metrics.get((kind, key, "spend_to_date_usd"))
        spend = int(spend_row["value"]) if spend_row and spend_row["value"] is not None else None
        facts[(kind, key)] = {"reached": reached, "advanced": advanced, "spend": spend,
                              "share": advanced / len(reached) if reached else None,
                              "cost": (spend / advanced) if spend and advanced else None}
    eligible = [f for f in facts.values() if len(f["reached"]) >= int(settings["min_reached"])]
    median_share = statistics.median([f["share"] for f in eligible]) if eligible else None
    costs = [f["cost"] for f in eligible if f["cost"]]
    median_cost = statistics.median(costs) if costs else None

    won_progress = {a: comparer.progress_date[a] for a, item in results.items()
                    if item["result"].stage == "Signed Agreement"}
    rows = []
    for kind, key, label in programs:
        f = facts[(kind, key)]
        comparison = lookup.get((kind, key))
        level = comparison["evidence_level"] if comparison else "Insufficient evidence"
        reached = f["reached"]
        first = min(reached.values()) if reached else None
        unexposed = comparison["group_b_total"] if comparison else 0
        exposed = comparison["group_a_total"] if comparison else 0
        enough_groups = unexposed >= int(comparison_rules["min_unexposed_accounts"]) and exposed >= int(comparison_rules["min_accounts_per_side"])
        spend_text = f" Spend to date: {money(f['spend'])}." if f["spend"] is not None else " Spend to date unknown."
        facts_text = ""
        if comparison:
            facts_text = " " + counts_text(kind, f"reached {'by' if kind == 'campaign' else 'through'} {label}",
                                           "moved into the pipeline", (comparison["group_a_with"], exposed,
                                                                        comparison["group_b_with"], unexposed))
        if len(reached) < int(settings["min_reached"]) or not first or (end - first).days < int(settings["min_days_since_first_exposure"]):
            verdict, evidence = "Insufficient evidence", "Insufficient evidence"
            if not reached:
                reason = "No target account reached yet."
            elif len(reached) < int(settings["min_reached"]):
                reason = f"Only {plural(len(reached), 'target account')} reached so far; too few to judge."
            else:
                reason = f"First exposure was {plural((end - first).days, 'day')} ago; too early to judge."
            facts_text = ""
        else:
            settled = [a for a, start in reached.items() if (end - start).days >= int(settings["stop_min_days_since_exposure"])]
            beyond_aware = [a for a in reached if results[a]["result"].stage_at(end) > rank("Aware")]
            before_won = [a for a, p in won_progress.items() if p and a in reached and reached[a] < p]
            evidence = level if level not in {"Insufficient evidence"} else "No clear difference"
            lower = comparison and level == "Association" and \
                comparison["group_a_with"] / max(exposed, 1) < comparison["group_b_with"] / max(unexposed, 1)
            if len(settled) >= int(settings["stop_min_reached"]) and not beyond_aware and not before_won:
                verdict = "Stop"
                reason = f"{plural(len(settled), 'account')} reached at least 6 months ago, and none has moved past Aware."
            elif level == "Consistent pattern":
                verdict = "Increase"
                reason = "Consistently seen before progress, for current clients and new relationships alike."
            elif enough_groups and (level == "No clear difference" or lower):
                verdict = "Reduce"
                reason = ("Accounts it reached progressed less often than those it did not." if lower else
                          "Accounts it reached progressed no more often than those it did not.")
            elif median_share is not None and f["share"] is not None and f["share"] < median_share \
                    and f["cost"] and median_cost and f["cost"] > median_cost:
                verdict = "Reduce"
                reason = (f"Since first reached, a smaller share of its accounts moved up a stage than for the median "
                          f"program ({f['share']:.0%} against {median_share:.0%}), at a higher cost per account that "
                          f"moved up ({money(f['cost'])} against {money(median_cost)}).")
            else:
                verdict = "Continue"
                if not enough_groups:
                    reason = ("It reaches almost every target account, so there is no comparison group; "
                              "no reason to change course.")
                    facts_text = f" It has reached {plural(len(reached), 'target account')}."
                elif level == "Association" and comparison["came_before_progress"] == 0:
                    reason = ("Seen more often in accounts that progressed, but mostly after that progress, "
                              "so it cannot explain it; no reason to change course.")
                elif level == "Association" and comparison["could_be_chance"]:
                    reason = "Seen more often in accounts that progressed, but a difference this size could be chance; keep it and watch."
                elif level == "Association":
                    segment_rows = [c for c in comparisons if c["activity_kind"] == kind and c["activity"] == key
                                    and c["comparison"] == "Advanced vs not" and c["segment"] != "All accounts"]
                    too_few = any(s["group_a_total"] < int(comparison_rules["min_accounts_per_side"]) or
                                  s["group_b_total"] < int(comparison_rules["min_accounts_per_side"]) for s in segment_rows)
                    reason = ("Seen more often in accounts that progressed, but too few current clients or new "
                              "relationships were reached to check both; keep it and watch." if too_few else
                              "Seen more often in accounts that progressed, but not within both current clients and "
                              "new relationships; keep it and watch.")
                else:
                    reason = "No rule for a change applies."
        rows.append({"as_of_date": as_of, "subject_type": kind, "subject_id": key, "verdict": verdict,
                     "evidence_level": evidence, "reason": f"{reason}{facts_text}{spend_text}",
                     "spend_to_date_usd": f["spend"], "accounts_reached": len(reached),
                     "evidence_refs": json.dumps([f"{kind}:{key}:Advanced vs not"])})
    return rows
