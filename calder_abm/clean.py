"""Reads dates, emails, names, domains and amounts the way each source writes them.

Every row from ingestion becomes one standard record with the same field names,
whatever the source called them (the column map lives in
config/source_mappings.yaml). Problems are never fixed silently: each one is
logged in data_quality_issues with what the system did about it.

Important decisions, in plain terms:
  * A date that cannot be read is left empty and logged. It is never guessed.
  * "04/03/2026" means 4 March in the events platform (it writes day first) and
    3 April in the CRM (it writes month first). Each source's habit is in the
    column map. Where the habit is unknown and both readings are possible, the
    date is logged as ambiguous and left empty.
  * Company names are compared in a cleaned form: lower case, punctuation removed,
    "&" read as "and", legal endings such as Inc or Ltd dropped.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import config, db

INTERNAL_DOMAIN = "calderhollis.com"

TEXT_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"]


# ----------------------------------------------------------------------------
# Small, testable helpers
# ----------------------------------------------------------------------------

def parse_when(value, fmt: str = "any") -> tuple[str | None, str, str | None]:
    """Read a date or time written in any of the formats the sources use.

    Returns (ISO time in UTC or None, precision, problem). precision is 'exact'
    when a time of day is known, 'day' for a date only, 'unknown' when unreadable.
    problem is None, 'missing', 'unreadable' or 'ambiguous'.
    """
    if value is None:
        return None, "unknown", "missing"
    text = str(value).strip()
    if not text:
        return None, "unknown", "missing"

    moment, precision = None, "day"
    if re.fullmatch(r"\d{9,11}", text):                      # Unix seconds
        moment = datetime.fromtimestamp(int(text), tz=timezone.utc)
        precision = "exact"
    elif re.fullmatch(r"\d{4}-\d{2}", text):                  # a month: 2025-03
        moment = datetime(int(text[:4]), int(text[5:7]), 1, 12, tzinfo=timezone.utc)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):             # a date
        moment = datetime.fromisoformat(text).replace(hour=12, tzinfo=timezone.utc)
    elif re.match(r"\d{4}-\d{2}-\d{2}T", text):                # a date and time
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            moment = moment.astimezone(timezone.utc)
            precision = "exact"
        except ValueError:
            moment = None
    elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text):      # slashes: who writes the month first?
        first, second, year = (int(part) for part in text.split("/"))
        if year < 100:
            year += 2000
        if fmt == "us":
            month, day = first, second
        elif fmt == "day_first":
            day, month = first, second
        elif first > 12 >= second:
            day, month = first, second
        elif second > 12 >= first:
            month, day = first, second
        elif first == second:
            month = day = first
        else:
            return None, "unknown", "ambiguous"
        try:
            moment = datetime(year, month, day, 12, tzinfo=timezone.utc)
        except ValueError:
            moment = None
    else:
        for pattern in TEXT_DATE_FORMATS:
            try:
                moment = datetime.strptime(text, pattern).replace(hour=12, tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    if moment is None or moment.year < 2000 or moment.year > 2100:
        return None, "unknown", "unreadable"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ"), precision, None


def to_datetime(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clean_email(value) -> str | None:
    text = str(value or "").strip().lower()
    if not text or "@" not in text or "." not in text.split("@")[-1] or " " in text:
        return None
    return text


def email_domain(email: str | None) -> str | None:
    return email.split("@")[-1] if email else None


def clean_domain(value) -> str | None:
    """'https://www.ABCCorp.com/about' -> 'abccorp.com'."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/")[0].split("?")[0]
    if text.startswith("www."):
        text = text[4:]
    return text if "." in text else None


def is_personal_domain(domain: str | None) -> bool:
    return bool(domain) and domain in set(config.identity_settings()["personal_email_domains"])


def normalize_company(name) -> str:
    """'ABC Corp Ltd.' -> 'abc'. Words like Group or Holdings are kept."""
    text = str(name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    endings = set(config.identity_settings()["legal_endings"])
    words = [w for w in text.split() if w not in endings]
    return " ".join(words)


def clean_money(value) -> int | None:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def map_relationship(value) -> str:
    text = str(value or "").strip().lower()
    mapping = {k.lower(): v for k, v in config.identity_settings()["relationship_values"].items()}
    return mapping.get(text, "Unknown")


def event_key(row: dict, spec: dict) -> str:
    event = spec.get("event", "*")
    if event == "*":
        return "*"
    if isinstance(event, list):
        return ":".join(str(row.get(column, "")).strip() for column in event)
    return str(row.get(event, "")).strip()


def map_signal(key: str, signal_map: dict) -> str | None:
    """Look up the signal type for an event value; 'held:*' and '*' match anything after them."""
    if key in signal_map:
        return signal_map[key]
    for pattern, signal_type in signal_map.items():
        if pattern == "*" or (pattern.endswith(":*") and key.startswith(pattern[:-1])):
            return signal_type
    return None


# ----------------------------------------------------------------------------
# Standard records
# ----------------------------------------------------------------------------

class Cleaner:
    """Turns raw rows into standard records and keeps a log of every problem."""

    def __init__(self):
        self.issues: list[dict] = []
        self.late_days = int(config.settings().get("late_arrival_days", 3))

    def issue(self, raw: dict, issue_type: str, severity: str, details: str, field: str | None = None) -> None:
        self.issues.append({"raw_record_id": raw["raw_record_id"] if raw else None,
                            "source": raw["source"] if raw else "system", "issue_type": issue_type,
                            "severity": severity, "field_name": field, "details": details})

    def clean_all(self, raw_records: list[dict]) -> dict[str, list[dict]]:
        mappings = config.source_mappings()
        out: dict[str, list[dict]] = {"master_list": [], "campaign_list": [], "spend": [], "crm_accounts": [],
                                      "crm_contacts": [], "opportunities": [], "activity": []}
        seen_payloads: dict[tuple, int] = {}
        for raw in raw_records:
            spec = mappings[raw["_key"]]
            row = raw["_row"]
            fingerprint = (raw["source_file"], raw["payload_json"])
            if fingerprint in seen_payloads:
                self.issue(raw, "duplicate_record", "Warning",
                           f"Exact copy of row {seen_payloads[fingerprint]} in {raw['source_file']}; ignored.")
                continue
            seen_payloads[fingerprint] = raw["source_row"]
            fields = {std: row.get(col) for std, col in spec.get("fields", {}).items()}
            record = {"raw_record_id": raw["raw_record_id"], "source": raw["source"],
                      "source_file": raw["source_file"], "source_row": raw["source_row"], "key": raw["_key"]}
            kind = spec["kind"]
            handler = getattr(self, f"_{kind}")
            cleaned = handler(raw, spec, fields, record)
            if cleaned is not None:
                out[kind].append(cleaned)
        return out

    # -- one handler per kind of source --------------------------------------
    def _master_list(self, raw, spec, f, record):
        domain = clean_domain(f.get("domain"))
        if not domain:
            self.issue(raw, "missing_company", "Warning", "Target account has no website; matching relies on its name.",
                       "website")
        relationship = map_relationship(f.get("relationship"))
        if relationship == "Unknown":
            written = str(f.get("relationship") or "").strip()
            self.issue(raw, "conflicting_value", "Info",
                       (f"Existing relationship '{written}' not recognised" if written else
                        "Existing relationship left blank") + "; recorded as Unknown and left out of the "
                       "current-client comparisons.", "existing_relationship")
        record.update({"account_id": str(f.get("account_id") or "").strip(),
                       "company_name": str(f.get("company_name") or "").strip(), "domain": domain,
                       "industry": f.get("industry"), "company_size": f.get("company_size"),
                       "location": ", ".join(x for x in [f.get("city"), f.get("region")] if x),
                       "country": (f.get("country") or "").strip().upper() or None,
                       "owner": f.get("owner"), "tier": int(f["tier"]) if str(f.get("tier") or "").isdigit() else None,
                       "relationship": relationship})
        return record

    def _campaign_list(self, raw, spec, f, record):
        budget = clean_money(f.get("planned_budget"))
        if budget is None:
            self.issue(raw, "spend_mismatch", "Info", "Campaign has no planned budget.", "planned_budget_usd")
        start, _, _ = parse_when(f.get("start_date"), "iso")
        end, _, _ = parse_when(f.get("end_date"), "iso")
        record.update({"campaign_id": f.get("campaign_id"), "name": f.get("name"), "channel": f.get("channel"),
                       "start_date": start[:10] if start else None, "end_date": end[:10] if end else None,
                       "planned_budget_usd": budget, "description": f.get("description")})
        return record

    def _spend(self, raw, spec, f, record):
        amount = clean_money(f.get("amount"))
        month = str(f.get("month") or "").strip()[:7]
        campaign = (f.get("campaign_id") or "").strip() or None
        if campaign is None:
            self.issue(raw, "spend_mismatch", "Warning",
                       f"Invoice {f.get('invoice_id')} ({amount or 0:,} USD) is not assigned to a campaign; "
                       "reported as unassigned spend.", "campaign_id")
        record.update({"invoice_id": f.get("invoice_id"), "month": month, "campaign_id": campaign,
                       "category": f.get("category") or "Other", "amount_usd": amount, "note": f.get("memo")})
        return record

    def _crm_accounts(self, raw, spec, f, record):
        record.update({"crm_account_id": (f.get("crm_account_id") or "").strip(),
                       "company_name": (f.get("company_name") or "").strip(),
                       "domain": clean_domain(f.get("domain")),
                       "country": (f.get("country") or "").strip().upper() or None,
                       "target_account_id": (f.get("target_account_id") or "").strip() or None,
                       "owner": f.get("owner")})
        return record

    def _crm_contacts(self, raw, spec, f, record):
        if not (f.get("crm_contact_id") or "").strip():
            self.issue(raw, "missing_contact_id", "Warning",
                       f"CRM contact {f.get('first_name')} {f.get('last_name')} has no contact ID.", "crm_contact_id")
        email = clean_email(f.get("email"))
        if not email:
            self.issue(raw, "missing_email", "Warning",
                       f"CRM contact {f.get('first_name')} {f.get('last_name')} has no email; matched by name only.",
                       "email")
        modified, _, _ = parse_when(f.get("modified"), spec.get("date_format", "any"))
        record.update({"crm_contact_id": (f.get("crm_contact_id") or "").strip() or None,
                       "crm_account_id": (f.get("crm_account_id") or "").strip(),
                       "first_name": (f.get("first_name") or "").strip(), "last_name": (f.get("last_name") or "").strip(),
                       "email": email, "job_title": (f.get("job_title") or "").strip() or None, "modified": modified})
        return record

    def _opportunities(self, raw, spec, f, record):
        fmt = spec.get("date_format", "any")
        created, _, _ = parse_when(f.get("created"), fmt)
        expected, _, _ = parse_when(f.get("expected_close"), fmt)
        closed, _, _ = parse_when(f.get("closed"), fmt)
        amount = clean_money(f.get("amount"))
        if amount is None:
            self.issue(raw, "other", "Info", f"Opportunity {f.get('opportunity_id')} has no amount; value unknown.",
                       "amount_usd")
        status = (f.get("status") or "").strip() or "Open"
        reason = (f.get("loss_reason") or "").strip() or None
        if status == "Lost" and not reason:
            self.issue(raw, "other", "Info", f"Opportunity {f.get('opportunity_id')} was lost with no reason recorded.",
                       "loss_reason")
        record.update({"opportunity_id": f.get("opportunity_id"), "crm_account_id": (f.get("crm_account_id") or "").strip(),
                       "name": f.get("name"), "service_line": f.get("service_line"), "amount_usd": amount,
                       "crm_stage": f.get("crm_stage"), "lead_source": (f.get("lead_source") or "").strip() or None,
                       "status": status, "loss_reason": reason,
                       "created": created, "expected_close": expected, "closed": closed})
        return record

    def _activity(self, raw, spec, f, record):
        row = raw["_row"]
        occurred, precision, problem = parse_when(row.get(spec["date_column"]), spec.get("date_format", "any"))
        if problem == "ambiguous":
            self.issue(raw, "ambiguous_date", "Warning",
                       f"Date '{row.get(spec['date_column'])}' could be read two ways; left empty.", spec["date_column"])
        elif problem:
            self.issue(raw, "unreadable_timestamp", "Warning",
                       f"Date '{row.get(spec['date_column']) or ''}' could not be read; the record is kept "
                       "but left off the timeline.", spec["date_column"])
        received = occurred
        late = 0
        received_column = spec.get("received_column")
        if received_column and occurred:
            got, _, _ = parse_when(row.get(received_column), "iso")
            if got:
                got_dt = to_datetime(got).replace(hour=0)
                received_dt = max(to_datetime(occurred), got_dt)
                received = received_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                days_late = (received_dt - to_datetime(occurred)).days
                if days_late > self.late_days:
                    late = 1
                    self.issue(raw, "late_arrival", "Info",
                               f"Arrived {days_late} days after it happened; placed on its real date.", received_column)
        key = event_key(row, spec)
        signal_type = map_signal(key, spec.get("signal_map") or {})
        email = clean_email(f.get("email"))
        if f.get("email") and not email:
            self.issue(raw, "missing_email", "Info", f"Email '{f.get('email')}' is not a valid address.", "email")
        attendees = []
        if f.get("attendees"):
            for item in str(f["attendees"]).split(";"):
                address = clean_email(item)
                if address and not address.endswith(INTERNAL_DOMAIN):
                    attendees.append(address)
            if not attendees:
                self.issue(raw, "missing_company", "Warning",
                           "Calendar entry has no outside attendees, so it cannot be tied to a company.", "attendees")
        record.update({
            "occurred_at": occurred, "time_precision": precision if occurred else "unknown",
            "received_at": received, "arrived_late": late, "event": key, "signal_type": signal_type,
            "company_name": (f.get("company_name") or "").strip() or None,
            "domain": clean_domain(f.get("domain")), "email": email,
            "person_name": (f.get("person_name") or "").strip() or None,
            "job_title": (f.get("job_title") or "").strip() or None,
            "crm_account_id": (f.get("crm_account_id") or "").strip() or None,
            "crm_contact_id": (f.get("crm_contact_id") or "").strip() or None,
            "campaign_id": ((f.get("campaign_id") or "").strip() or spec.get("campaign")
                            or (spec.get("campaign_by_event") or {}).get(key) or spec.get("default_campaign") or None),
            "details": (f.get("details") or "").strip() or None,
            "text": (f.get("text") or "").strip() or None,
            "attendees": attendees,
            "asset": f.get("asset"), "consent": f.get("consent"),
            "impressions": clean_money(f.get("impressions")), "engagements": clean_money(f.get("engagements")),
        })
        return record

    def save(self, conn: sqlite3.Connection) -> None:
        db.insert(conn, "data_quality_issues", self.issues)
        conn.commit()


def campaign_lookup(records: dict[str, list[dict]]) -> dict[str, dict]:
    return {c["campaign_id"]: c for c in records["campaign_list"] if c.get("campaign_id")}


def save_reference_rows(conn: sqlite3.Connection, records: dict[str, list[dict]]) -> None:
    """Campaigns and monthly spend go straight into their tables."""
    db.insert(conn, "campaigns", [
        {"campaign_id": c["campaign_id"], "name": c["name"], "channel": c["channel"], "start_date": c["start_date"],
         "end_date": c["end_date"], "planned_budget_usd": c["planned_budget_usd"], "description": c["description"]}
        for c in records["campaign_list"]])
    known = {c["campaign_id"] for c in records["campaign_list"]}
    seen_invoices = set()
    rows = []
    for s in records["spend"]:
        if s["invoice_id"] in seen_invoices:
            continue
        seen_invoices.add(s["invoice_id"])
        rows.append({"raw_record_id": s["raw_record_id"],
                     "campaign_id": s["campaign_id"] if s["campaign_id"] in known else None,
                     "month": s["month"], "category": s["category"] if s["category"] in {
                         "Media", "Events", "Content", "Agency", "Partner programs", "Tools", "Other"} else "Other",
                     "amount_usd": s["amount_usd"] or 0, "note": s["note"]})
    db.insert(conn, "marketing_spend", rows)
    conn.commit()


def payload(raw_json: str) -> dict:
    return json.loads(raw_json)
