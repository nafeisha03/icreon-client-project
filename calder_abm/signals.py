"""Turns each matched record into typed signals, using the signal model.

Also:
  * reads free-text notes and email replies with the notes reader;
  * marks duplicates, such as one meeting logged in both the calendar and the CRM;
  * skips email "opens" made by mail apps within a minute of sending;
  * adds system signals such as "No response, 45+ days".

Only records linked to a target account (confidently or as "probable") become
signals. Everything else stays visible on the data quality page.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import timedelta

from . import config, db
from .clean import to_datetime
from .notes import read_text

# Events that happen once per account per day, whoever recorded them.
ACCOUNT_LEVEL = {
    "discovery_meeting", "executive_meeting", "follow_up_meeting", "meeting_cancelled", "meeting_scheduled",
    "proposal_requested", "pricing_requested", "rfp_received", "proposal_sent", "legal_joined",
    "procurement_started", "verbal_commitment", "contract_negotiation", "contract_signed", "opportunity_lost",
    "need_identified", "budget_confirmed", "timeline_confirmed", "competitor_mentioned", "no_competitor_confirmed",
    "decision_process_confirmed", "budget_cut", "leadership_change", "priorities_changed", "procurement_delay",
    "website_visit", "ad_shown", "linkedin_engagement", "news_trigger", "job_posting", "tech_partner_referral",
}
SOURCE_PREFERENCE = ["meetings", "crm", "events", "email", "website", "partners", "notes", "external", "marketing"]
MEETINGS = {"discovery_meeting", "executive_meeting", "follow_up_meeting"}
STRENGTH_ORDER = ["None", "Very weak", "Weak", "Medium", "Strong", "Very strong", "Decisive"]


def is_meaningful(signal_type: str) -> bool:
    """A positive signal of Medium strength or above from the account, or any meeting held."""
    info = config.signal_types()[signal_type]
    if info.get("meeting_held"):
        return True
    return (info["direction"] == "Positive" and info["category"] == "Engagement"
            and STRENGTH_ORDER.index(info["strength"]) >= STRENGTH_ORDER.index("Medium"))


class SignalBuilder:
    def __init__(self, resolved: dict, campaigns: set[str]):
        self.model = config.signal_types()
        self.contacts = resolved["contacts"]
        self.campaigns = campaigns
        self.signals: list[dict] = []
        self.issues: list[dict] = []
        self.open_questions: list[dict] = []
        self.sends: dict[tuple, list] = defaultdict(list)

    def make(self, rec: dict, signal_type: str, *, contact_id=None, method="Structured field", quote=None,
             description=None, raw_record_id="from_rec") -> dict:
        info = self.model[signal_type]
        campaign = rec.get("campaign_id")
        signal = {
            "raw_record_id": rec["raw_record_id"] if raw_record_id == "from_rec" else raw_record_id,
            "account_id": rec.get("account_id"), "contact_id": contact_id if contact_id is not None else rec.get("contact_id"),
            "campaign_id": campaign if campaign in self.campaigns else None,
            "occurred_at": rec.get("occurred_at"), "time_precision": rec.get("time_precision", "unknown"),
            "received_at": rec.get("received_at"), "arrived_late": rec.get("arrived_late", 0),
            "source": rec["source"], "signal_type": signal_type,
            "description": description or rec.get("details") or info["label"],
            "strength": info["strength"],
            "sentiment": {"Positive": "Positive", "Negative": "Negative"}.get(info["direction"], "Neutral"),
            "match_confidence": rec.get("match_confidence"), "extraction_method": method,
            "evidence_quote": quote, "is_duplicate": 0, "duplicate_of": None, "is_derived": 0,
            "_key": rec.get("key"), "_group": None,
        }
        self.signals.append(signal)
        return signal

    def issue(self, rec, issue_type, severity, details, field=None):
        self.issues.append({"raw_record_id": rec["raw_record_id"], "source": rec["source"], "issue_type": issue_type,
                            "severity": severity, "field_name": field, "details": details})

    # -- per record ----------------------------------------------------------
    def from_record(self, rec: dict) -> None:
        if rec.get("match_decision") not in {"Linked", "Linked as probable"}:
            return
        key = rec["key"]
        if key == "meeting_notes":
            return self.read_note(rec)
        if key == "email_events":
            return self.email(rec)
        if key == "meetings":
            return self.meeting(rec)
        if key == "partner_referrals":
            return self.referral(rec)
        if key == "linkedin_ads":
            if (rec.get("impressions") or 0) > 0:
                self.make(rec, "ad_shown", description=f"LinkedIn ads shown ({rec['impressions']} impressions this month)")
            if (rec.get("engagements") or 0) > 0:
                self.make(rec, "linkedin_engagement", description=f"{rec['engagements']} LinkedIn engagement(s) with our content")
            return
        if rec.get("signal_type"):
            self.make(rec, rec["signal_type"])

    def read_note(self, rec: dict) -> None:
        reading = read_text(rec.get("text"))
        for signal_type, sentence in reading.facts:
            self.make(rec, signal_type, method="Phrase rule", quote=sentence,
                      description=f"Read from a {str(rec.get('details') or 'note').lower()}")
        for sentence in reading.open_questions:
            self.open_questions.append({"account_id": rec["account_id"], "occurred_at": rec.get("occurred_at"),
                                        "received_at": rec.get("received_at"), "sentence": sentence,
                                        "raw_record_id": rec["raw_record_id"]})
            self.issue(rec, "hedged_statement", "Info",
                       f"Hedged wording kept as an open question, not a fact: \"{sentence}\"", "text")

    def email(self, rec: dict) -> None:
        signal_type = rec.get("signal_type")
        moment = to_datetime(rec.get("occurred_at"))
        if signal_type == "outreach_sent" and moment:
            self.sends[(rec.get("email"), rec.get("campaign_id"))].append(moment)
        if signal_type == "email_opened" and moment:
            sends = self.sends.get((rec.get("email"), rec.get("campaign_id")), [])
            if any(timedelta(0) <= moment - s <= timedelta(seconds=60) for s in sends):
                self.issue(rec, "other", "Info", "Opened within a minute of sending: most likely a mail app, "
                                                 "not a person; not counted.", "event_type")
                return
        if signal_type == "email_replied":
            reading = read_text(rec.get("text"))
            types = [t for t, _ in reading.facts]
            if "negative_reply" in types:
                quote = dict(reading.facts)["negative_reply"]
                self.make(rec, "negative_reply", method="Phrase rule", quote=quote, description="Email reply")
            else:
                self.make(rec, "email_replied", description="Email reply")
            for signal_type2, sentence in reading.facts:
                if signal_type2 != "negative_reply":
                    self.make(rec, signal_type2, method="Phrase rule", quote=sentence, description="Read from an email reply")
            return
        if signal_type:
            self.make(rec, signal_type)

    def meeting(self, rec: dict) -> None:
        signal_type = rec.get("signal_type")
        if not signal_type:
            return
        people = rec.get("attendee_contacts") or [rec.get("contact_id")]
        group = f"meeting-{rec['raw_record_id']}"
        first = None
        for contact_id in people:
            signal = self.make(rec, signal_type, contact_id=contact_id)
            signal["_group"] = group
            first = first or signal
            if signal is not first:
                signal["_attendee_of"] = first
        if signal_type in MEETINGS:
            counsel = [c for c in people if c and self.contacts.get(c, {}).get("buying_role") == "Legal Reviewer"]
            if counsel:
                name = self.contacts[counsel[0]]["name"]
                self.make(rec, "legal_joined", contact_id=counsel[0],
                          description=f"{name} ({self.contacts[counsel[0]]['job_title']}) attended the meeting")

    def referral(self, rec: dict) -> None:
        consent = str(rec.get("consent") or "").strip().upper()
        if consent == "N":
            self.issue(rec, "other", "Warning", "Referral reported by the partner, but the client never agreed "
                                                "to be referred; not counted as a signal.", "client_agreed")
            return
        partner = rec.get("details") or "a technology partner"
        self.make(rec, "tech_partner_referral", description=f"Referred by {partner}")
        if rec.get("text"):
            self.make(rec, "need_identified", quote=rec["text"], description=f"Need stated in the referral from {partner}")

    def from_opportunity(self, opp: dict, rec_lookup: dict) -> None:
        rec = rec_lookup.get(opp["_raw_record_id"])
        if not rec or not opp.get("closed_date"):
            return
        base = {"raw_record_id": opp["_raw_record_id"], "account_id": opp["account_id"], "contact_id": None,
                "campaign_id": None, "occurred_at": f"{opp['closed_date']}T12:00:00Z", "time_precision": "day",
                "received_at": f"{opp['closed_date']}T12:00:00Z", "arrived_late": 0, "source": "crm",
                "match_confidence": opp["_match_confidence"], "key": "crm_opportunities"}
        if opp["status"] == "Won":
            self.make(base, "contract_signed", description=f"CRM marks {opp['name']} as won")
        elif opp["status"] == "Lost":
            reason = opp.get("loss_reason") or "no reason recorded"
            self.make(base, "opportunity_lost", description=f"CRM marks {opp['name']} as lost ({reason})")

    # -- after all records ---------------------------------------------------
    def mark_duplicates(self) -> None:
        def rank(s):
            source = SOURCE_PREFERENCE.index(s["source"]) if s["source"] in SOURCE_PREFERENCE else 99
            return (source, s["extraction_method"] != "Structured field", s["raw_record_id"] or 0)

        groups: dict[tuple, list[dict]] = defaultdict(list)
        for s in self.signals:
            if s.get("_attendee_of") or not s["account_id"] or not s["occurred_at"]:
                continue
            day = s["occurred_at"][:10]
            if s["signal_type"] in ACCOUNT_LEVEL:
                key = (s["account_id"], s["signal_type"], day)
            else:
                key = (s["account_id"], s["signal_type"], day, s["contact_id"], s["campaign_id"])
            groups[key].append(s)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=rank)
            keeper = members[0]
            for other in members[1:]:
                other["is_duplicate"] = 1
                other["_duplicate_of"] = keeper
        for s in self.signals:
            if s.get("_attendee_of"):
                s["is_duplicate"] = 1
                s["_duplicate_of"] = s["_attendee_of"]

    def add_silence(self, data_end: str) -> None:
        """'No response, 45+ days' markers, once an account has reached the pipeline."""
        threshold = int(config.thresholds()["stalled_after_quiet_days"])
        pipeline_types = {t for t, info in self.model.items()
                          if info.get("supports_stage") in set(config.pipeline_stages())}
        by_account: dict[str, list[dict]] = defaultdict(list)
        for s in self.signals:
            if s["account_id"] and s["occurred_at"] and not s["is_duplicate"]:
                by_account[s["account_id"]].append(s)
        end = to_datetime(f"{data_end}T23:59:59Z")
        for account_id, items in by_account.items():
            items.sort(key=lambda s: s["occurred_at"])
            entered = next((to_datetime(s["occurred_at"]) for s in items if s["signal_type"] in pipeline_types), None)
            if not entered:
                continue
            if any(s["signal_type"] in {"contract_signed", "opportunity_lost"} for s in items):
                closed = min(to_datetime(s["occurred_at"]) for s in items
                             if s["signal_type"] in {"contract_signed", "opportunity_lost"})
            else:
                closed = end
            times = sorted({to_datetime(s["occurred_at"]) for s in items
                            if is_meaningful(s["signal_type"]) and to_datetime(s["occurred_at"]) >= entered})
            times = times or [entered]
            for left, right in zip(times, times[1:] + [closed]):
                if (right - left).days >= threshold:
                    when = left + timedelta(days=threshold)
                    if when > closed:
                        continue
                    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.signals.append({
                        "raw_record_id": None, "account_id": account_id, "contact_id": None, "campaign_id": None,
                        "occurred_at": stamp, "time_precision": "day", "received_at": stamp, "arrived_late": 0,
                        "source": "system", "signal_type": "no_response_45d",
                        "description": f"No meaningful activity since {left.date().isoformat()}",
                        "strength": self.model["no_response_45d"]["strength"], "sentiment": "Negative",
                        "match_confidence": None, "extraction_method": "Structured field", "evidence_quote": None,
                        "is_duplicate": 0, "duplicate_of": None, "is_derived": 1, "_key": "system", "_group": None})

    def finish(self) -> list[dict]:
        self.signals.sort(key=lambda s: (s["occurred_at"] or "9999", s["raw_record_id"] or 0, s["signal_type"]))
        for index, s in enumerate(self.signals, start=1):
            s["signal_id"] = f"SIG-{index:06d}"
        for s in self.signals:
            if s.get("_duplicate_of"):
                s["duplicate_of"] = s["_duplicate_of"]["signal_id"]
        return self.signals


def build_signals(conn: sqlite3.Connection, records: dict[str, list[dict]], resolved: dict, data_end: str) -> dict:
    campaigns = {c["campaign_id"] for c in records["campaign_list"]}
    builder = SignalBuilder(resolved, campaigns)
    activity = sorted(records["activity"], key=lambda r: (r.get("occurred_at") or "9999", r["raw_record_id"]))
    for rec in activity:
        builder.from_record(rec)
    rec_lookup = {r["raw_record_id"]: r for r in records["opportunities"]}
    for opp in resolved["opportunities"]:
        builder.from_opportunity(opp, rec_lookup)
    builder.mark_duplicates()
    builder.add_silence(data_end)
    signals = builder.finish()
    columns = ["signal_id", "raw_record_id", "account_id", "contact_id", "campaign_id", "occurred_at", "time_precision",
               "received_at", "arrived_late", "source", "signal_type", "description", "strength", "sentiment",
               "match_confidence", "extraction_method", "evidence_quote", "is_duplicate", "duplicate_of", "is_derived"]
    # Duplicates point at the record they repeat; that link is added once every signal exists.
    db.insert(conn, "signals", [{c: (None if c == "duplicate_of" else s[c]) for c in columns} for s in signals])
    conn.executemany("UPDATE signals SET duplicate_of = ? WHERE signal_id = ?",
                     [(s["duplicate_of"], s["signal_id"]) for s in signals if s["duplicate_of"]])
    db.insert(conn, "data_quality_issues", builder.issues)
    conn.commit()
    return {"signals": signals, "open_questions": builder.open_questions}
