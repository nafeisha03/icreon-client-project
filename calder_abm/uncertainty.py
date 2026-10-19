"""Confidence checks, and what we know, infer and do not know.

Confidence starts at High and drops one level, down to Low, for each check that
fails. Two situations force "Insufficient evidence" whatever the checks say:
sources that contradict each other about the stage, and a pipeline stage that
rests only on probable company matches. Every drop comes with its reason.

Statements are labelled:
  KNOWN     a recorded fact, with the signal it comes from
  INFERRED  a conclusion, with the rule that produced it
  UNKNOWN   a question the data cannot answer yet
"""
from __future__ import annotations

from datetime import datetime

from . import config
from .clean import to_datetime
from .stages import StageResult, rank
from .text import day, plural

LEVELS = ["High", "Medium", "Low"]
DECIDERS = {"Decision Maker", "Executive Sponsor"}


def confidence(r: StageResult, people_roles: set[str], as_of: str) -> tuple[str, list[str], list[str]]:
    """(level, reasons, ids of failed checks)."""
    if r.conflicts:
        return "Insufficient evidence", list(r.conflicts), ["INS-CONFLICT"]
    if r.probable_only():
        return ("Insufficient evidence",
                [f"Every signal behind {r.active_stage} came through a probable company match, so we cannot be "
                 "sure the activity came from this company."], ["INS-PROBABLE"])
    if r.stage == "Target":
        if not r.signals:
            return "Medium", ["nothing is recorded, and an empty record is weak evidence: contact may simply not "
                              "be logged"], []
        return "Medium", ["only our own emails, ads or outside news are recorded; no response yet, though a "
                          "conversation may simply not be logged"], []
    end = to_datetime(f"{as_of}T23:59:59Z")
    limits = config.thresholds()
    failed, reasons = [], []
    key = r.key_signal
    closed = r.stage in {"Signed Agreement", "Lost"}
    if key and not closed:
        age = (end - to_datetime(key["occurred_at"])).days
        if age > int(limits["recent_signal_days"]):
            failed.append("CONF-RECENT")
            reasons.append(f"the key signal is {plural(age, 'day')} old")
    if r.evidence and not closed:
        sources = {s["source"] for s in r.evidence}
        people = {s.get("contact_id") or "company" for s in r.evidence}
        if len(sources) == 1 and len(people) == 1:
            failed.append("CONF-CORROBORATED")
            reasons.append("the key fact comes from one source and one person only")
    if key and key.get("match_confidence") == "Medium":
        failed.append("CONF-IDENTITY")
        reasons.append("the key signal was linked through a probable company match")
    if key and key.get("extraction_method") != "Structured field":
        failed.append("CONF-STRUCTURED")
        reasons.append("the key signal was read from free text, not a structured field")
    if r.gaps:
        failed.append("CONF-GAPS")
        reasons.append(f"a step is missing: {r.gaps[0]}")
    if not closed and rank(r.active_stage) >= rank("Active Conversation") and not (people_roles & DECIDERS):
        failed.append("CONF-DECIDER")
        reasons.append("no decision maker or executive sponsor has appeared in any interaction")
    level = LEVELS[min(len(failed), 2)]
    if not reasons:
        reasons = ["recent, corroborated, structured evidence from confidently matched records"]
    return level, reasons, failed


def unknowns(r: StageResult, all_signals: list[dict], people_roles: set[str], opportunity: dict | None,
             open_questions: list[dict], as_of: str) -> list[dict]:
    """Questions that stay UNKNOWN until a recorded fact answers them."""
    if r.stage in {"Signed Agreement", "Lost"}:
        return []
    stage = r.active_stage
    end = to_datetime(f"{as_of}T23:59:59Z")
    types_seen = {s["signal_type"] for s in all_signals}
    out = []
    for q in config.stage_rules()["unknowns"]:
        if rank(stage) < rank(q["from_stage"]):
            continue
        if q.get("only_if_signals") and not (types_seen & set(q["only_if_signals"])):
            continue
        answers = [s for s in all_signals if s["signal_type"] in set(q.get("answered_by_signals", []))]
        if q.get("answered_within_days"):
            answers = [s for s in answers if (end - to_datetime(s["occurred_at"])).days <= int(q["answered_within_days"])]
        answered = bool(answers) or bool(people_roles & set(q.get("answered_by_roles", [])))
        if q.get("answered_by_crm") == "expected_close_date" and opportunity and opportunity.get("expected_close_date"):
            answered = True
        if not answered:
            out.append({"id": q["id"], "statement": q["question"]})
    for item in open_questions:
        out.append({"id": "UNK-HEDGED", "statement": f"Unconfirmed: a note on {day(item['occurred_at'])} says "
                                                     f"\"{item['sentence']}\""})
    return out


def known_facts(r: StageResult, account_name: str, contacts: dict, as_of: str, limit: int = 5) -> list[dict]:
    """The recorded facts that matter most for the current position, each with its evidence."""
    model = config.signal_types()
    picked: list[dict] = []
    seen = set()
    candidates = list(r.evidence[-2:])
    candidates += [s for s in r.signals if model[s["signal_type"]]["category"] in {"Deal fact", "Negative"}
                   and s["signal_type"] != "no_response_45d"][-3:]
    if r.signals:
        candidates.append(r.signals[-1])
    for s in candidates:
        if s["signal_id"] in seen:
            continue
        seen.add(s["signal_id"])
        person = contacts.get(s.get("contact_id") or "", {}).get("name")
        who = person or f"Someone at {account_name}"
        label = model[s["signal_type"]]["label"].lower()
        source = {"crm": "CRM", "meetings": "calendar", "notes": "partner note", "email": "email platform",
                  "events": "events platform", "website": "website analytics", "partners": "partner referral report",
                  "external": "news feed", "marketing": "LinkedIn report", "system": "system check"}.get(s["source"], s["source"])
        text = f"{who}: {label} on {day(s['occurred_at'])} ({source})"
        if s.get("evidence_quote"):
            text += f": \"{s['evidence_quote']}\""
        if s.get("match_confidence") == "Medium":
            text += " [probable company match]"
        picked.append({"statement": text, "evidence": [s["signal_id"]]})
    return picked[:limit]
