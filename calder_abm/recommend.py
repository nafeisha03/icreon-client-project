"""Picks the next best action for each account from the playbook.

Rules are checked in the order listed in config/action_playbook.yaml; the first
one that matches produces the recommendation. Each recommendation names the
person (or the role we still need), the action, the channel, a reason with real
dates, and the confidence. If the right person is not in our contacts, the
recommendation names the role and says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import config
from .clean import to_datetime
from .signals import is_meaningful
from .stages import StageResult
from .text import day, plural

MEETINGS = {"discovery_meeting", "executive_meeting", "follow_up_meeting"}
SENIORITY = ["C-level", "VP", "Director", "Manager", "Staff", "Unknown"]


@dataclass
class Facts:
    """What the playbook needs to know about one account at one review point."""
    account: dict
    r: StageResult
    confidence: str
    signals: list                 # visible, duplicates removed
    all_signals: list             # visible, including meeting attendees and duplicates
    contacts: dict                # every contact, by id
    as_of: str
    opportunity: dict | None = None
    end: datetime = field(init=False)

    def __post_init__(self):
        self.end = to_datetime(f"{self.as_of}T23:59:59Z")

    def latest(self, *types) -> dict | None:
        found = [s for s in self.signals if s["signal_type"] in types]
        return found[-1] if found else None

    def days_since(self, signal_or_time) -> int | None:
        if signal_or_time is None:
            return None
        moment = signal_or_time if isinstance(signal_or_time, datetime) else to_datetime(signal_or_time["occurred_at"])
        return (self.end - moment).days

    def after(self, signal: dict, predicate) -> list:
        return [s for s in self.signals if s["occurred_at"] > signal["occurred_at"] and predicate(s)]

    @property
    def meetings_held(self) -> list:
        return [s for s in self.signals if s["signal_type"] in MEETINGS]

    @property
    def people_seen(self) -> list[dict]:
        ids = {s.get("contact_id") for s in self.all_signals if s.get("contact_id")}
        return [self.contacts[i] for i in ids if i in self.contacts]

    @property
    def roles_seen(self) -> set[str]:
        engaged = {s.get("contact_id") for s in self.all_signals
                   if s.get("contact_id") and config.signal_types()[s["signal_type"]]["category"] == "Engagement"}
        return {self.contacts[i]["buying_role"] for i in engaged if i in self.contacts}

    def account_contacts(self) -> list[dict]:
        """The account's contacts known by the review point (people first seen later are left out)."""
        cutoff = f"{self.as_of}T23:59:59Z"
        return [c for c in self.contacts.values() if c.get("account_id") == self.account["account_id"]
                and (c.get("first_seen_at") or "") <= cutoff]

    def contact_with_role(self, *roles) -> dict | None:
        seen = {c["contact_id"] for c in self.people_seen}
        options = [c for c in self.account_contacts() if c["buying_role"] in roles]
        options.sort(key=lambda c: (c["contact_id"] not in seen, SENIORITY.index(c.get("seniority") or "Unknown")))
        return options[0] if options else None

    def most_senior_engaged(self) -> dict | None:
        people = [c for c in self.people_seen if c.get("account_id") == self.account["account_id"]]
        people.sort(key=lambda c: SENIORITY.index(c.get("seniority") or "Unknown"))
        return people[0] if people else None

    def engaged_recently(self, days: int) -> set[str]:
        return {s["contact_id"] for s in self.all_signals if s.get("contact_id")
                and config.signal_types()[s["signal_type"]]["category"] == "Engagement"
                and config.signal_types()[s["signal_type"]]["direction"] == "Positive"
                and self.days_since(s) is not None and self.days_since(s) <= days}

    def trigger(self) -> dict | None:
        return self.latest("news_trigger", "job_posting")


def rule_matches(rule_id: str, f: Facts, rule: dict) -> str | None:
    """None if the rule does not apply; otherwise the reason, in one sentence with real dates."""
    stage, r = f.r.stage, f.r
    name = f.account["company_name"]
    days = int(rule.get("days", 0))
    if rule_id == "A1" and stage == "Signed Agreement":
        return f"{name} signed on {day(r.terminal_date)}."
    if rule_id == "A2" and stage == "Lost":
        return f"{name} was marked lost on {day(r.terminal_date)}."
    if rule_id == "A3" and f.confidence == "Insufficient evidence":
        return "The records contradict each other or rest on uncertain matches, so the real position is unclear."
    if rule_id == "A4" and stage == "Verbal Commitment":
        progress = f.latest("verbal_commitment", "contract_negotiation", *MEETINGS, "legal_joined", "procurement_started")
        quiet = f.days_since(progress)
        if quiet is not None and quiet >= days:
            warning = f.latest("budget_cut", "leadership_change", "procurement_delay", "priorities_changed")
            extra = ""
            if warning and warning["occurred_at"] >= progress["occurred_at"]:
                quote = f": \"{warning['evidence_quote']}\"" if warning.get("evidence_quote") else ""
                extra = (f" A warning sign was recorded on {day(warning['occurred_at'])}, "
                         f"{config.signal_label(warning['signal_type']).lower()}{quote}")
                extra = extra if extra.endswith(".") else extra + "."
            return (f"They said yes on {day(r.reached.get('Verbal Commitment'))}, and nothing has moved toward "
                    f"a signature for {plural(quiet, 'day')}.{extra}")
    if rule_id == "A5" and stage == "Proposal / RFP":
        legal = f.latest("legal_joined")
        if legal and f.days_since(legal) >= days:
            return f"Legal joined on {day(legal['occurred_at'])} and there has been no legal follow-up for {plural(f.days_since(legal), 'day')}."
    if rule_id == "A6" and stage == "Proposal / RFP":
        procurement = f.latest("procurement_started")
        if procurement and f.days_since(procurement) >= days and not f.after(procurement, lambda s: is_meaningful(s["signal_type"])):
            return f"Procurement started on {day(procurement['occurred_at'])} with no follow-up for {plural(f.days_since(procurement), 'day')}."
    if rule_id == "A7" and stage == "Proposal / RFP":
        ask = f.latest("proposal_requested", "pricing_requested")
        if ask and f.days_since(ask) >= days and not f.after(ask, lambda s: s["signal_type"] == "proposal_sent"):
            who = f.contacts.get(ask.get("contact_id") or "", {}).get("name", "They")
            return f"{who} asked for a proposal on {day(ask['occurred_at'])}; none has been sent after {plural(f.days_since(ask), 'day')}."
    if rule_id == "A8" and stage == "Proposal / RFP":
        sent = f.latest("proposal_sent")
        if sent and f.days_since(sent) >= days and not f.after(sent, lambda s: is_meaningful(s["signal_type"])):
            return f"The proposal went out on {day(sent['occurred_at'])} and there has been no response for {plural(f.days_since(sent), 'day')}."
    if rule_id == "A9" and stage == "Stalled":
        return f"Nothing meaningful has happened since {day(r.last_meaningful)} ({plural(r.quiet_days, 'day')}), while at {r.last_active_stage}."
    if rule_id == "A10" and stage == "Active Conversation" and not (f.roles_seen & {"Decision Maker", "Executive Sponsor"}):
        return "Conversations have begun, but no decision maker or executive sponsor has appeared in any interaction."
    if rule_id == "A11" and stage == "Active Conversation":
        last_meeting = f.meetings_held[-1] if f.meetings_held else None
        if last_meeting and f.days_since(last_meeting) >= days:
            return f"The last meeting was on {day(last_meeting['occurred_at'])}, {plural(f.days_since(last_meeting), 'day')} ago, with no follow-up meeting since."
    if rule_id == "A12" and stage == "Qualified Lead" and not f.meetings_held:
        need = f.latest("need_identified")
        when = day(need["occurred_at"]) if need else day(r.reached.get("Qualified Lead"))
        return f"A need was identified on {when}, but no meeting has been held yet."
    if rule_id == "A13" and stage == "Interested" and not f.meetings_held:
        people = f.engaged_recently(int(rule.get("days", 30)))
        if len(people) >= int(rule.get("people", 3)):
            return f"{plural(len(people), 'person', 'people')} from {name} engaged in the last 30 days, and no meeting has been held."
    if rule_id == "A14" and stage == "Interested" and not f.meetings_held:
        return f"{name} took a deliberate step on {day(r.reached.get('Interested'))}, but no meeting has been held."
    if rule_id == "A15" and f.account.get("existing_relationship") == "Current client, other services" \
            and not f.meetings_held and stage in {"Target", "Aware", "Interested"}:
        return f"{name} is a current client of the firm's other services, and no AI conversation has been recorded."
    if rule_id == "A16" and stage == "Aware":
        return f"{name} has seen us (first on {day(r.reached.get('Aware'))}) but has not taken a deliberate step."
    if rule_id == "A17" and stage == "Target" and f.trigger():
        t = f.trigger()
        return f"No contact yet, but there is a possible trigger: {t['description']} ({day(t['occurred_at'])})."
    if rule_id == "A18" and stage == "Target":
        return "No recorded contact yet, and no trigger to act on."
    if rule_id == "A19":
        steps = [s for s in f.signals if s["signal_type"] not in {"ad_shown", "outreach_sent", "no_response_45d"}]
        latest = steps[-1] if steps else None
        return (f"On track at {stage}; the latest step was {config.signal_label(latest['signal_type']).lower()} "
                f"on {day(latest['occurred_at'])}." if latest else f"On track at {stage}.")
    return None


def target_for(rule_id: str, f: Facts, rule: dict) -> tuple[str | None, str, str | None]:
    """(contact_id, role shown, unknown sentence if the person is missing)."""
    roles = {
        "A1": ("Executive Sponsor",), "A2": ("Decision Maker", "Executive Sponsor"),
        "A4": ("Decision Maker", "Executive Sponsor"), "A5": ("Legal Reviewer",), "A6": ("Procurement",),
        "A8": ("Decision Maker", "Executive Sponsor"), "A10": ("Influencer",), "A17": ("Executive Sponsor",),
    }
    if rule_id == "A3":
        return None, f"Account owner ({f.account.get('account_owner') or 'unassigned'})", None
    if rule_id == "A18":
        return None, "Role to identify", None
    contact = None
    if rule_id in roles:
        contact = f.contact_with_role(*roles[rule_id])
    elif rule_id == "A7":
        ask = f.latest("proposal_requested", "pricing_requested")
        contact = f.contacts.get(ask.get("contact_id") or "") if ask else None
    elif rule_id == "A11":
        contact = f.contacts.get(f.meetings_held[-1].get("contact_id") or "") if f.meetings_held else None
    elif rule_id == "A12":
        need = f.latest("need_identified", "tech_partner_referral")
        contact = f.contacts.get(need.get("contact_id") or "") if need else None
    if contact is None and rule_id not in {"A17"}:
        contact = f.most_senior_engaged()
    role_label = rule["who"]
    if contact:
        title = contact.get("job_title") or contact.get("buying_role")
        return contact["contact_id"], f"{contact['name']} ({title})", None
    missing = f"No known {rule['who'].lower()} contact at {f.account['company_name']}; ask a known contact for an introduction."
    return None, role_label, missing


def recommend(f: Facts) -> dict:
    playbook = config.playbook()["playbook"]
    for rule in playbook:
        reason = rule_matches(rule["id"], f, rule)
        if reason:
            contact_id, role, missing = target_for(rule["id"], f, rule)
            return {"rule_id": rule["id"], "action": rule["action"], "channel": rule["channel"],
                    "target_contact_id": contact_id, "target_role": role, "reason": reason, "missing": missing}
    raise RuntimeError("The playbook has no catch-all rule")
