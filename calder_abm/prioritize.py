"""Places each account in an attention tier, using the written rules in config/action_playbook.yaml.

Tiers, checked top to bottom: Won, Find out, Act now, Push, Reduce spend, Nurture.
Within a tier, accounts are sorted by known opportunity value, then by target tier.
"""
from __future__ import annotations

from . import config
from .recommend import Facts
from .text import day, plural

TIER_ORDER = ["Act now", "Find out", "Push", "Nurture", "Reduce spend", "Won"]
OVERDUE_RULES = {"A4", "A5", "A6", "A7", "A8"}
EXPOSURES = {"outreach_sent", "ad_shown"}


def attention_tier(f: Facts, rule_id: str) -> tuple[str, str]:
    """(tier, reason)."""
    settings = config.playbook()["tier_settings"]
    r, stage = f.r, f.r.stage
    if stage == "Signed Agreement":
        return "Won", "Signed: hand over to delivery; no further marketing spend."
    if f.confidence == "Insufficient evidence":
        return "Find out", "The evidence is contradictory or uncertain; confirm the real status first."
    if stage in {"Proposal / RFP", "Verbal Commitment"}:
        return "Act now", f"At {stage}: a decision is close."
    if rule_id in OVERDUE_RULES:
        return "Act now", "A follow-up is overdue."
    if stage in {"Qualified Lead", "Active Conversation"} and r.trend == "Progressing":
        return "Push", f"At {stage} and progressing."
    if stage == "Stalled" and (r.quiet_days or 0) < int(settings["stalled_push_max_days"]):
        return "Push", f"Stalled for {plural(r.quiet_days, 'day')} at {r.last_active_stage}: still worth a push."
    trigger = f.trigger()
    if trigger and f.account.get("target_tier") == 1 and stage != "Lost" \
            and f.days_since(trigger) <= int(settings["new_trigger_days"]):
        return "Push", f"Tier 1 account with a new trigger on {day(trigger['occurred_at'])}."
    if f.account.get("existing_relationship") == "Current client, other services" and not f.meetings_held \
            and stage in {"Target", "Aware", "Interested"}:
        return "Push", "Current client of other services with no AI conversation yet."
    if stage == "Stalled":
        return "Reduce spend", f"Stalled for {plural(r.quiet_days, 'day')}."
    if stage == "Lost":
        return "Reduce spend", "Lost."
    lookback = int(settings["exposure_lookback_days"])
    exposures = [s for s in f.all_signals if s["signal_type"] in EXPOSURES and f.days_since(s) <= lookback]
    engaged = [s for s in f.signals if config.signal_types()[s["signal_type"]]["category"] == "Engagement"
               and config.signal_types()[s["signal_type"]]["direction"] == "Positive" and f.days_since(s) <= lookback]
    # Days since the account was first reached; silence is only judged after one full sales cycle.
    first_exposed = max((f.days_since(s) for s in f.all_signals if s["signal_type"] in EXPOSURES), default=0)
    long_enough = first_exposed >= int(settings.get("min_days_since_first_exposure", 0))
    if stage in {"Target", "Aware"} and len(exposures) >= int(settings["min_exposures_without_engagement"]) \
            and not engaged and long_enough:
        return "Reduce spend", (f"{plural(len(exposures), 'campaign exposure')} in the last 6 months and no "
                                f"engagement; first reached {plural(first_exposed, 'day')} ago, more than one "
                                "sales cycle.")
    return "Nurture", "Keep in light-touch marketing."
