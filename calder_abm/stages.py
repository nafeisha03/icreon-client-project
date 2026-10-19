"""Applies the stage and trend rules to one account's timeline.

The stage is the furthest step the account has evidence for, on or before the
review point. Our own outreach never moves it (except a proposal we sent), and
news or job posts never move it either. On top of that:

  * Stalled replaces the stage after 45 quiet days at Qualified Lead or later,
    and clears when meaningful activity resumes;
  * a verbal commitment can still fall through: warning signs after it keep the
    stage but are noted, and only an explicit loss moves an account to Lost;
  * a missing step (a proposal with no meeting on record) is allowed but noted;
  * when the CRM claims a later stage than anything recorded supports, and the
    account has been quiet for 60 days, the sources contradict each other.

Each stage change is recorded with when it happened, when we learned it and
which rule fired.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config
from .clean import to_datetime
from .signals import STRENGTH_ORDER, is_meaningful
from .text import day, plural
from .timeline import visible

LADDER = ["Target", "Aware", "Interested", "Qualified Lead", "Active Conversation", "Proposal / RFP",
          "Verbal Commitment", "Signed Agreement"]
STAGE_RULE = {"Aware": "STG-AWARE", "Interested": "STG-INTERESTED", "Qualified Lead": "STG-QUALIFIED",
              "Active Conversation": "STG-CONVERSATION", "Proposal / RFP": "STG-PROPOSAL",
              "Verbal Commitment": "STG-VERBAL", "Signed Agreement": "STG-SIGNED", "Stalled": "STG-STALLED",
              "Lost": "STG-LOST", "Target": "STG-TARGET"}
WARNINGS = {"budget_cut", "leadership_change", "procurement_delay", "priorities_changed", "contact_left"}
CRM_STAGE = {"Qualified Lead": "Qualified Lead", "Active Conversation": "Active Conversation",
             "Proposal / RFP": "Proposal / RFP", "Verbal Commitment": "Verbal Commitment",
             "Signed Agreement": "Signed Agreement"}


def rank(stage: str | None) -> int:
    return LADDER.index(stage) if stage in LADDER else -1


@dataclass
class StageResult:
    stage: str = "Target"
    last_active_stage: str | None = None
    stage_note: str | None = None
    rule_id: str = "STG-TARGET"
    key_signal: dict | None = None
    evidence: list = field(default_factory=list)
    history: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    reached: dict = field(default_factory=dict)          # stage -> first date it was reached
    last_meaningful: datetime | None = None
    quiet_days: int | None = None
    terminal_date: datetime | None = None
    trend: str = "Unclear"
    trend_reason: str = "No recorded activity to judge from"
    trend_rule: str = "TRD-UNCLEAR"
    signals: list = field(default_factory=list)

    @property
    def active_stage(self) -> str:
        """The stage to judge by: for Stalled, the stage it stalled at."""
        return self.last_active_stage if self.stage == "Stalled" else self.stage

    def probable_only(self) -> bool:
        """Every signal behind a pipeline stage came through a probable match."""
        if rank(self.active_stage) < rank("Qualified Lead") or self.stage in {"Signed Agreement", "Lost"}:
            return False
        return bool(self.evidence) and all(s.get("match_confidence") == "Medium" for s in self.evidence)

    def stage_at(self, when: datetime) -> int:
        """The furthest ladder position reached by a date (stalls and losses do not undo progress)."""
        best = 0
        for stage, reached in self.reached.items():
            if reached <= when:
                best = max(best, rank(stage))
        return best


def assess(signals: list[dict], as_of: str, crm_stage: str | None = None) -> StageResult:
    """Stage, history, gaps, conflicts and trend for one account at one review point.

    signals: the account's signals visible at the review point, duplicates removed.
    crm_stage: the CRM's own stage for the open opportunity, when it should be checked.
    """
    model = config.signal_types()
    limits = config.thresholds()
    end = to_datetime(f"{as_of}T23:59:59Z")
    # Only what had happened, and reached us, by the end of the review day.
    items = sorted((s for s in signals if visible(s, as_of)), key=lambda s: (s["occurred_at"], s["signal_id"]))
    r = StageResult(signals=items)

    current, terminal, terminal_signal = "Target", None, None
    by_stage: dict[str, list] = defaultdict(list)
    after_loss = []
    for s in items:
        kind = s["signal_type"]
        when = to_datetime(s["occurred_at"])
        if terminal == "Lost" and kind != "opportunity_lost" and is_meaningful(kind):
            after_loss.append(s)
        if kind == "opportunity_lost":
            if terminal is None:
                terminal, terminal_signal, r.terminal_date = "Lost", s, when
                r.history.append(change(current, "Lost", s, "STG-LOST"))
            continue
        if kind == "contract_signed":
            if terminal != "Signed Agreement":
                by_stage["Signed Agreement"].append(s)
                r.history.append(change(current, "Signed Agreement", s, "STG-SIGNED"))
                r.reached.setdefault("Signed Agreement", when)
                terminal, terminal_signal, r.terminal_date = "Signed Agreement", s, when
                current = "Signed Agreement"
            continue
        supports = model[kind].get("supports_stage")
        if supports not in LADDER:
            continue
        by_stage[supports].append(s)
        if terminal is None and rank(supports) > rank(current):
            r.history.append(change(current, supports, s, STAGE_RULE[supports]))
            r.reached.setdefault(supports, when)
            current = supports

    # Quiet spells: stalls that came and went, and whether the account is stalled now.
    meaningful = [to_datetime(s["occurred_at"]) for s in items if is_meaningful(s["signal_type"])]
    r.last_meaningful = max(meaningful) if meaningful else None
    pipeline_start = min((d for st, d in r.reached.items() if rank(st) >= rank("Qualified Lead")), default=None)
    stall_days = int(limits["stalled_after_quiet_days"])
    if pipeline_start and terminal is None:
        marks = sorted({d for d in meaningful if d >= pipeline_start} | {pipeline_start})
        for left, right in zip(marks, marks[1:]):
            if (right - left).days >= stall_days:
                stalled_at = left + timedelta(days=stall_days)
                r.history.append({"from_stage": stage_on(r, left), "to_stage": "Stalled",
                                  "effective_at": stamp(stalled_at), "learned_at": stamp(stalled_at),
                                  "rule_id": "STG-STALLED", "evidence_signal_ids": [], "_order": stamp(stalled_at)})
                r.history.append({"from_stage": "Stalled", "to_stage": stage_on(r, right),
                                  "effective_at": stamp(right), "learned_at": stamp(right),
                                  "rule_id": "STG-RESUMED", "evidence_signal_ids": [], "_order": stamp(right)})
    last = r.last_meaningful or (r.reached.get(current) if current != "Target" else None)
    r.quiet_days = (end - last).days if last else None

    if terminal:
        r.stage, r.rule_id = terminal, STAGE_RULE[terminal]
        r.last_active_stage = current if terminal == "Lost" else None
        r.key_signal = terminal_signal
        r.evidence = by_stage.get(current, []) + [terminal_signal]
        if after_loss:
            r.stage_note = "newer activity after the loss"
            r.conflicts.append(f"The account was marked lost on {day(r.terminal_date)}, but there was meaningful "
                               f"activity afterwards ({after_loss[-1]['description']} on {day(after_loss[-1]['occurred_at'])}).")
    elif rank(current) >= rank("Qualified Lead") and r.quiet_days is not None and r.quiet_days >= stall_days:
        r.stage, r.last_active_stage, r.rule_id = "Stalled", current, "STG-STALLED"
        stalled_at = last + timedelta(days=stall_days)
        r.history.append({"from_stage": current, "to_stage": "Stalled", "effective_at": stamp(stalled_at),
                          "learned_at": stamp(stalled_at), "rule_id": "STG-STALLED", "evidence_signal_ids": [],
                          "_order": stamp(stalled_at)})
        r.evidence = by_stage.get(current, [])
        r.key_signal = r.evidence[-1] if r.evidence else None
        r.stage_note = f"at {current}"
    else:
        r.stage, r.rule_id = current, STAGE_RULE[current]
        r.evidence = by_stage.get(current, [])
        r.key_signal = r.evidence[-1] if r.evidence else None

    active = r.active_stage
    if active == "Proposal / RFP" and r.stage == "Proposal / RFP":
        legal = any(s["signal_type"] == "legal_joined" for s in items)
        procurement = any(s["signal_type"] == "procurement_started" for s in items)
        if legal or procurement:
            r.stage_note = ("legal and procurement reviewing" if legal and procurement
                            else "legal reviewing" if legal else "procurement reviewing")
    if r.stage == "Verbal Commitment":
        since = r.reached.get("Verbal Commitment")
        warnings = [s for s in items if s["signal_type"] in WARNINGS and to_datetime(s["occurred_at"]) >= since]
        if warnings:
            r.stage_note = f"warning: {model[warnings[-1]['signal_type']]['label'].lower()}"

    # Missing steps on the way here.
    if rank(active) >= rank("Proposal / RFP") and not by_stage.get("Active Conversation"):
        r.gaps.append("a proposal with no meeting on record")
    if rank(active) >= rank("Verbal Commitment") and not by_stage.get("Proposal / RFP") and r.stage != "Signed Agreement":
        r.gaps.append("a verbal commitment with no proposal on record")

    # Do the CRM and the evidence disagree?
    if crm_stage and terminal is None:
        claimed = CRM_STAGE.get(crm_stage)
        quiet = r.quiet_days if r.quiet_days is not None else 9999
        if claimed and rank(claimed) > rank(active) and quiet >= int(limits["conflict_quiet_days"]):
            r.conflicts.append(f"The CRM says {crm_stage}, but nothing recorded supports more than {active}, "
                               f"and nothing meaningful has happened for {plural(quiet, 'day')}.")

    r.history.sort(key=lambda h: h["_order"])
    set_trend(r, end, model, limits)
    return r


def change(from_stage: str, to_stage: str, s: dict, rule_id: str) -> dict:
    return {"from_stage": from_stage, "to_stage": to_stage, "effective_at": s["occurred_at"],
            "learned_at": s.get("received_at") or s["occurred_at"], "rule_id": rule_id,
            "evidence_signal_ids": [s["signal_id"]], "_order": s["occurred_at"]}


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def stage_on(r: StageResult, when: datetime) -> str:
    return LADDER[r.stage_at(when)]


def set_trend(r: StageResult, end: datetime, model: dict, limits: dict) -> None:
    """Checked top to bottom; the first rule that fits wins."""
    look_back = timedelta(days=int(limits["progressing_lookback_days"]))
    if r.stage == "Stalled":
        r.trend, r.trend_rule = "Stalled", "TRD-STALLED"
        r.trend_reason = (f"No meaningful activity since {day(r.last_meaningful)} "
                          f"({plural(r.quiet_days, 'day')})" if r.last_meaningful else "No meaningful activity recorded")
        return
    if r.stage in {"Signed Agreement", "Lost"}:
        r.trend, r.trend_rule = "Steady", "TRD-CLOSED"
        r.trend_reason = f"{'Signed' if r.stage == 'Signed Agreement' else 'Lost'} on {day(r.terminal_date)}"
        return
    if not r.signals:
        r.trend, r.trend_rule, r.trend_reason = "Unclear", "TRD-UNCLEAR", "No recorded activity to judge from"
        return
    recent = [s for s in r.signals if end - to_datetime(s["occurred_at"]) <= look_back]
    moves = [h for h in r.history if h["rule_id"] not in {"STG-STALLED", "STG-RESUMED"}
             and end - to_datetime(h["effective_at"]) <= look_back and h["to_stage"] != "Target"]
    strong = [s for s in recent if model[s["signal_type"]]["direction"] == "Positive"
              and model[s["signal_type"]]["category"] == "Engagement"
              and STRENGTH_ORDER.index(s["strength"]) >= STRENGTH_ORDER.index("Strong")]
    if moves:
        r.trend, r.trend_rule = "Progressing", "TRD-PROGRESSING"
        r.trend_reason = f"Moved to {moves[-1]['to_stage']} on {day(moves[-1]['effective_at'])}"
        return
    if strong:
        r.trend, r.trend_rule = "Progressing", "TRD-PROGRESSING"
        r.trend_reason = f"{config.signal_label(strong[-1]['signal_type'])} on {day(strong[-1]['occurred_at'])}"
        return
    negative = [s for s in recent if model[s["signal_type"]]["direction"] == "Negative"]
    if negative:
        r.trend, r.trend_rule = "Slowing", "TRD-SLOWING"
        r.trend_reason = f"{model[negative[-1]['signal_type']]['label']} on {day(negative[-1]['occurred_at'])}"
        return
    if rank(r.stage) >= rank("Qualified Lead") and (r.quiet_days or 0) >= int(limits["slowing_quiet_days_pipeline"]):
        r.trend, r.trend_rule = "Slowing", "TRD-SLOWING"
        r.trend_reason = f"Quiet for {plural(r.quiet_days, 'day')} at {r.stage}"
        return
    if rank(r.stage) in {rank("Aware"), rank("Interested")}:
        engaged = [to_datetime(s["occurred_at"]) for s in r.signals
                   if model[s["signal_type"]]["category"] == "Engagement" and model[s["signal_type"]]["direction"] == "Positive"]
        last = max(engaged) if engaged else None
        if not last or (end - last).days >= int(limits["slowing_quiet_days_marketing"]):
            quiet = (end - last).days if last else None
            r.trend, r.trend_rule = "Slowing", "TRD-SLOWING"
            r.trend_reason = f"No engagement for {plural(quiet, 'day')}" if quiet is not None else "No engagement recorded"
            return
    r.trend, r.trend_rule = "Steady", "TRD-STEADY"
    last_signal = r.signals[-1]
    r.trend_reason = (f"Still active, without moving forward; latest: {config.signal_label(last_signal['signal_type']).lower()} "
                      f"on {day(last_signal['occurred_at'])}")
