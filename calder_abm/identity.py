"""Decides which company and which person each record belongs to.

The marketing target list is the master list of companies. Every other record,
including the CRM's own company records, is matched to it. A record is linked
only on solid evidence, and the reason is always saved:

  High    the CRM record carries a valid target account ID, or the website or
          email domain matches the company's domain  -> Linked
  Medium  the cleaned name matches exactly one company and the country agrees
          or is missing                              -> Linked as "probable"
  Low     the country differs, the name fits several companies, the domain and
          the name disagree, or the name is only similar -> Needs review
  None    a personal email only, an internet provider's name, or nothing at all
                                                     -> Not linked

People are matched more cautiously still: the same email is the same person;
with no email, the same full name within the same company is a probable match;
names alone never merge people across companies.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from difflib import SequenceMatcher

from . import config, db
from .clean import clean_domain, email_domain, is_personal_domain, normalize_company


def infer_role(title: str | None) -> str:
    text = (title or "").lower()
    for rule in config.identity_settings()["role_keywords"]:
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in rule["keywords"]):
            return rule["role"]
    return "Unknown"


def infer_seniority(title: str | None) -> str:
    text = (title or "").lower()
    for rule in config.identity_settings()["seniority_keywords"]:
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in rule["keywords"]):
            return rule["seniority"]
    return "Unknown"


def normalize_person(name: str | None) -> str:
    return re.sub(r"[^a-z ]+", "", (name or "").lower()).strip()


class Resolver:
    def __init__(self):
        settings = config.identity_settings()
        self.threshold = float(settings["similar_name_threshold"])
        self.providers = {p.lower() for p in settings["network_provider_names"]}
        self.overrides = config.identity_overrides()
        self.accounts: dict[str, dict] = {}
        self.by_domain: dict[str, set] = defaultdict(set)
        self.by_name: dict[str, set] = defaultdict(set)
        self.probable_domains: dict[str, str] = {}   # domains learned only from probable CRM records
        self.crm_links: dict[str, dict] = {}
        self.contacts: dict[str, dict] = {}
        self.contact_by_email: dict[str, str] = {}
        self.contact_by_name: dict[str, dict] = defaultdict(dict)
        self.matches: list[dict] = []
        self.issues: list[dict] = []
        self.aliases: list[dict] = []
        self._alias_keys: set = set()
        self._contact_counter = 0

    # -- master list -----------------------------------------------------------
    def load_accounts(self, master: list[dict]) -> list[dict]:
        rows = []
        for r in master:
            if not r["account_id"]:
                continue
            account = {
                "account_id": r["account_id"], "company_name": r["company_name"], "domain": r["domain"],
                "industry": r["industry"], "company_size": r["company_size"], "location": r["location"],
                "country": r["country"], "account_owner": r["owner"], "target_tier": r["tier"],
                "existing_relationship": r["relationship"], "current_stage": "Target"}
            self.accounts[r["account_id"]] = account
            rows.append(account)
            self.add_alias(r["account_id"], "name", normalize_company(r["company_name"]), "target list")
            if r["domain"]:
                self.add_alias(r["account_id"], "domain", r["domain"], "target list")
        return rows

    def add_alias(self, account_id: str, alias_type: str, value: str | None, learned_from: str) -> None:
        if not value:
            return
        key = (account_id, alias_type, value)
        if key in self._alias_keys:
            return
        self._alias_keys.add(key)
        (self.by_name if alias_type == "name" else self.by_domain)[value].add(account_id)
        self.aliases.append({"account_id": account_id, "alias_type": alias_type, "alias_value": value,
                             "learned_from": learned_from})

    # -- company matching ------------------------------------------------------
    def similar_accounts(self, cleaned: str) -> set:
        """Accounts whose name is only similar: a shortened name or a near spelling."""
        hits = set()
        words = cleaned.split()
        for name, account_ids in self.by_name.items():
            other = name.split()
            if not words or not other:
                continue
            prefix = (len(words) < len(other) and other[:len(words) - 1] == words[:-1]
                      and other[len(words) - 1].startswith(words[-1]))
            prefix = prefix or (len(other) < len(words) and words[:len(other)] == other)
            close = SequenceMatcher(None, cleaned, name).ratio() >= self.threshold
            head = " ".join(other[:len(words)])
            close = close or (len(words) < len(other) and SequenceMatcher(None, cleaned, head).ratio() >= self.threshold)
            if prefix or close:
                hits |= account_ids
        return hits

    def override_for(self, source_file: str, company_name: str | None) -> dict | None:
        if not company_name:
            return None
        cleaned = normalize_company(company_name)
        for o in self.overrides:
            if o.get("source_file") == source_file and normalize_company(o.get("company_name")) == cleaned:
                return o
        return None

    def match_company(self, rec: dict) -> dict:
        """Decide which target account a record belongs to. Returns the decision and its reason."""
        name = rec.get("company_name")
        override = self.override_for(rec.get("source_file"), name)
        if override:
            account_id = override.get("account_id")
            who = f"{override.get('decided_by', 'a person')} on {override.get('decided_on', 'an earlier date')}"
            if account_id and account_id != "none" and account_id in self.accounts:
                return self.result(account_id, "High", "Linked", f"confirmed by {who}", reviewed=override)
            return self.result(None, "None", "Not linked", f"{who} decided it is not a target account", reviewed=override)

        crm_id = rec.get("crm_account_id")
        if crm_id and crm_id in self.crm_links and self.crm_links[crm_id]["account_id"]:
            link = self.crm_links[crm_id]
            decision = "Linked" if link["confidence"] == "High" else "Linked as probable"
            return self.result(link["account_id"], link["confidence"], decision,
                               f"CRM record {crm_id}: {link['reason']}")

        domains, via_email = [], False
        for domain in [rec.get("domain")] + [email_domain(rec.get("email"))] + [email_domain(a) for a in rec.get("attendees", [])]:
            if domain and not is_personal_domain(domain) and domain not in domains:
                domains.append(domain)
        if rec.get("email") and email_domain(rec["email"]) in domains:
            via_email = True
        hits = set()
        for domain in domains:
            hits |= self.by_domain.get(domain, set())
        if len(hits) == 1:
            account_id = next(iter(hits))
            if name:
                name_hits = self.by_name.get(normalize_company(name), set())
                if name_hits and account_id not in name_hits:
                    return self.result(None, "Low", "Needs review",
                                       f"domain points to {self.label(account_id)} but the name '{name}' points to "
                                       f"{', '.join(self.label(a) for a in sorted(name_hits))}",
                                       candidates=sorted(hits | name_hits))
            domain = next(d for d in domains if account_id in self.by_domain.get(d, set()))
            what = "email domain" if via_email else "domain"
            return self.result(account_id, "High", "Linked", f"{what} {domain} matches {self.label(account_id)}")
        if len(hits) > 1:
            return self.result(None, "Low", "Needs review", "the domains point to several companies",
                               candidates=sorted(hits))
        learned = {self.probable_domains[d] for d in domains if d in self.probable_domains}
        if len(learned) == 1:
            account_id = next(iter(learned))
            domain = next(d for d in domains if self.probable_domains.get(d) == account_id)
            return self.result(account_id, "Medium", "Linked as probable",
                               f"domain {domain} was seen only on a CRM record matched to {self.label(account_id)} by name")

        if not name:
            if rec.get("email") and is_personal_domain(email_domain(rec["email"])):
                return self.result(None, "None", "Not linked", "personal email address only; kept as unattributed")
            return self.result(None, "None", "Not linked", "no company information")
        if name.strip().lower() in self.providers:
            return self.result(None, "None", "Not linked", f"'{name}' is an internet provider, not a company")
        cleaned = normalize_company(name)
        exact = self.by_name.get(cleaned, set())
        if len(exact) == 1:
            account_id = next(iter(exact))
            account_country = self.accounts[account_id]["country"]
            if rec.get("country") and account_country and rec["country"] != account_country:
                return self.result(None, "Low", "Needs review",
                                   f"name matches {self.label(account_id)} but the country differs "
                                   f"({rec['country']} vs {account_country})", candidates=[account_id])
            return self.result(account_id, "Medium", "Linked as probable",
                               f"company name '{name}' matches {self.label(account_id)}; no domain to confirm it")
        if len(exact) > 1:
            return self.result(None, "Low", "Needs review", f"the name '{name}' matches {len(exact)} companies",
                               candidates=sorted(exact))
        similar = self.similar_accounts(cleaned)
        if similar:
            return self.result(None, "Low", "Needs review",
                               f"the name '{name}' is only similar to "
                               f"{', '.join(self.label(a) for a in sorted(similar))}", candidates=sorted(similar))
        return self.result(None, "None", "Not linked", f"'{name}' is not on the target list")

    def label(self, account_id: str) -> str:
        a = self.accounts[account_id]
        return f"{a['company_name']} ({account_id})"

    @staticmethod
    def result(account_id, confidence, decision, reason, candidates=None, reviewed=None) -> dict:
        return {"account_id": account_id, "confidence": confidence, "decision": decision, "reason": reason,
                "candidates": candidates, "reviewed": reviewed}

    # -- CRM companies ---------------------------------------------------------
    def resolve_crm_accounts(self, crm_accounts: list[dict]) -> None:
        # Records with a target ID or a domain first, so aliases learned from them can help.
        ordered = sorted(crm_accounts, key=lambda r: (not r["target_account_id"], not r["domain"]))
        for r in ordered:
            if r["target_account_id"] and r["target_account_id"] in self.accounts:
                m = self.result(r["target_account_id"], "High", "Linked",
                                f"CRM record carries target ID {r['target_account_id']}")
                if r["domain"] and r["domain"] not in self.by_domain:
                    self.add_alias(r["target_account_id"], "domain", r["domain"], "CRM company record")
            else:
                m = self.match_company({"company_name": r["company_name"], "domain": r["domain"],
                                        "country": r["country"], "source_file": r["source_file"]})
            self.crm_links[r["crm_account_id"]] = {"account_id": m["account_id"], "confidence": m["confidence"],
                                                   "reason": m["reason"]}
            if m["confidence"] == "High" and m["account_id"]:
                self.add_alias(m["account_id"], "name", normalize_company(r["company_name"]), "CRM company record")
            self.record_match(r, m, input_company=r["company_name"], input_domain=r["domain"],
                              input_country=r["country"])

    # -- people -------------------------------------------------------------
    def new_contact(self, account_id, name, email, title, confidence, crm_contact_id=None, seen_at=None) -> str:
        self._contact_counter += 1
        contact_id = f"CON-{self._contact_counter:04d}"
        self.contacts[contact_id] = {
            "contact_id": contact_id, "account_id": account_id, "name": name, "email": email,
            "job_title": title, "department": None, "seniority": infer_seniority(title),
            "buying_role": infer_role(title), "crm_contact_id": crm_contact_id,
            "match_confidence": confidence, "first_seen_at": seen_at, "last_seen_at": seen_at,
            "_title_seen_at": seen_at or ""}
        if email:
            self.contact_by_email[email] = contact_id
        if account_id and name:
            self.contact_by_name[account_id].setdefault(normalize_person(name), contact_id)
        return contact_id

    def load_crm_contacts(self, crm_contacts: list[dict]) -> None:
        for r in crm_contacts:
            link = self.crm_links.get(r["crm_account_id"], {"account_id": None, "confidence": "None",
                                                            "reason": "unknown CRM company"})
            name = f"{r['first_name']} {r['last_name']}".strip()
            if r["email"] and r["email"] in self.contact_by_email:
                contact_id = self.contact_by_email[r["email"]]
                reason = "same email as an existing contact"
            else:
                confidence = "High" if r["email"] and link["confidence"] == "High" else (
                    "Medium" if link["account_id"] else "Low")
                contact_id = self.new_contact(link["account_id"], name, r["email"], r["job_title"], confidence,
                                              r["crm_contact_id"], r["modified"])
                reason = "CRM contact record"
            m = self.result(link["account_id"], link["confidence"],
                            "Linked" if link["confidence"] == "High" else
                            "Linked as probable" if link["account_id"] else "Not linked",
                            f"CRM company {r['crm_account_id']}: {link['reason']}")
            if m["decision"] == "Not linked":
                m["confidence"] = "None" if link["confidence"] == "None" else "Low"
                m["decision"] = "Needs review" if m["confidence"] == "Low" else "Not linked"
            self.record_match(r, m, input_email=r["email"], contact_id=contact_id,
                              contact_confidence=self.contacts[contact_id]["match_confidence"], contact_reason=reason)
            # A work email on a CRM contact tells us a domain the company uses.
            domain = email_domain(r["email"])
            if domain and link["account_id"] and not is_personal_domain(domain) and domain not in self.by_domain:
                if link["confidence"] == "High":
                    self.add_alias(link["account_id"], "domain", domain, "CRM contact emails")
                else:
                    self.probable_domains.setdefault(domain, link["account_id"])

    def match_person(self, rec: dict, company: dict, email: str | None, name: str | None,
                     title: str | None) -> tuple[str | None, str | None, str | None]:
        """(contact_id, confidence, reason) for one person in a record."""
        account_id = company["account_id"]
        when = rec.get("occurred_at")
        if email and email in self.contact_by_email:
            contact_id = self.contact_by_email[email]
            self.touch(contact_id, title, when, rec)
            return contact_id, "High", "same email address"
        if account_id and name:
            known = self.contact_by_name[account_id].get(normalize_person(name))
            if known:
                self.touch(known, title, when, rec)
                return known, "Medium", "same name at the same company"
        if account_id and email and not is_personal_domain(email_domain(email)):
            contact_id = self.new_contact(account_id, name or email.split("@")[0].replace(".", " ").title(),
                                          email, title, "High" if company["confidence"] == "High" else "Medium",
                                          seen_at=when)
            return contact_id, self.contacts[contact_id]["match_confidence"], f"new person, first seen in {rec['source_file']}"
        if account_id and name:
            contact_id = self.new_contact(account_id, name, None, title, "Medium", seen_at=when)
            return contact_id, "Medium", f"new person named in {rec['source_file']}; no email to confirm"
        return None, None, None

    def touch(self, contact_id: str, title: str | None, when: str | None, rec: dict) -> None:
        contact = self.contacts[contact_id]
        if when:
            contact["first_seen_at"] = min(filter(None, [contact["first_seen_at"], when]))
            contact["last_seen_at"] = max(filter(None, [contact["last_seen_at"], when]))
        if title and contact["job_title"] and title != contact["job_title"]:
            if (when or "") >= contact["_title_seen_at"]:
                self.issues.append({"raw_record_id": rec["raw_record_id"], "source": rec["source"],
                                    "issue_type": "conflicting_value", "severity": "Info", "field_name": "job_title",
                                    "details": f"{contact['name']}: title '{contact['job_title']}' in one source, "
                                               f"'{title}' in a newer one; the newer title is shown."})
                contact["job_title"], contact["_title_seen_at"] = title, when or ""
                contact["buying_role"] = infer_role(title)
                contact["seniority"] = infer_seniority(title)
        elif title and not contact["job_title"]:
            contact["job_title"], contact["buying_role"] = title, infer_role(title)
            contact["seniority"] = infer_seniority(title)

    # -- activity records ------------------------------------------------------
    def resolve_activity(self, records: list[dict]) -> None:
        """Attach account, confidence and person to every activity record (in place)."""
        for rec in sorted(records, key=lambda r: (r.get("occurred_at") or "9999", r["raw_record_id"])):
            company = self.match_company(rec)
            rec["account_id"] = company["account_id"]
            rec["match_confidence"] = company["confidence"]
            rec["match_decision"] = company["decision"]
            contact_id = contact_confidence = contact_reason = None
            rec["attendee_contacts"] = []
            if company["account_id"]:
                if rec.get("attendees"):
                    for address in rec["attendees"]:
                        guess = address.split("@")[0].replace(".", " ").replace("_", " ").title()
                        cid, conf, why = self.match_person(rec, company, address, guess, None)
                        if cid:
                            rec["attendee_contacts"].append(cid)
                            if contact_id is None:
                                contact_id, contact_confidence, contact_reason = cid, conf, why
                else:
                    contact_id, contact_confidence, contact_reason = self.match_person(
                        rec, company, rec.get("email"), rec.get("person_name"), rec.get("job_title"))
                    if contact_id is None and rec.get("crm_contact_id"):
                        contact_id = next((c["contact_id"] for c in self.contacts.values()
                                           if c["crm_contact_id"] == rec["crm_contact_id"]), None)
                        contact_confidence, contact_reason = ("High", "CRM contact ID") if contact_id else (None, None)
            rec["contact_id"] = contact_id
            self.record_match(rec, company, input_company=rec.get("company_name"), input_domain=rec.get("domain"),
                              input_email=rec.get("email"), contact_id=contact_id,
                              contact_confidence=contact_confidence, contact_reason=contact_reason)
            self.log_match_issue(rec, company)

    def log_match_issue(self, rec: dict, company: dict) -> None:
        if company["decision"] == "Needs review":
            self.issues.append({"raw_record_id": rec["raw_record_id"], "source": rec["source"],
                                "issue_type": "uncertain_match", "severity": "Warning", "field_name": "company",
                                "details": f"Not linked; waiting for a person to review: {company['reason']}."})
        elif company["decision"] == "Not linked":
            reason = company["reason"]
            if "personal email" in reason:
                kind, severity = "personal_email_domain", "Info"
            elif reason == "no company information" and rec["source"] == "website":
                kind, severity = "anonymous_visit", "Info"
            elif "internet provider" in reason:
                kind, severity = "anonymous_visit", "Info"
            else:
                kind, severity = "unmatched_company", "Info"
            self.issues.append({"raw_record_id": rec["raw_record_id"], "source": rec["source"],
                                "issue_type": kind, "severity": severity, "field_name": "company",
                                "details": f"Not linked: {reason}."})

    # -- opportunities -----------------------------------------------------------
    def resolve_opportunities(self, opportunities: list[dict]) -> list[dict]:
        rows = []
        for r in opportunities:
            link = self.crm_links.get(r["crm_account_id"])
            if not link or not link["account_id"]:
                m = self.result(None, "Low", "Needs review", f"CRM company {r['crm_account_id']} is not linked")
                self.record_match(r, m)
                self.issues.append({"raw_record_id": r["raw_record_id"], "source": r["source"],
                                    "issue_type": "unmatched_company", "severity": "Warning", "field_name": "crm_account_id",
                                    "details": f"Opportunity {r['opportunity_id']} belongs to an unlinked CRM company."})
                continue
            decision = "Linked" if link["confidence"] == "High" else "Linked as probable"
            self.record_match(r, self.result(link["account_id"], link["confidence"], decision,
                                             f"CRM company {r['crm_account_id']}: {link['reason']}"))
            status = r["status"] if r["status"] in {"Open", "Won", "Lost"} else "Open"
            rows.append({"opportunity_id": r["opportunity_id"], "account_id": link["account_id"], "name": r["name"],
                         "service_line": r["service_line"], "estimated_value_usd": r["amount_usd"],
                         "crm_stage": r["crm_stage"], "crm_lead_source": r["lead_source"], "status": status,
                         "loss_reason": r["loss_reason"] if status == "Lost" and r["loss_reason"] in {
                             "Chose another firm", "Budget cut or freeze", "Leadership change", "Priorities changed",
                             "Procurement stalled", "No decision", "Other"} else None,
                         "created_date": (r["created"] or "")[:10] or None,
                         "expected_close_date": (r["expected_close"] or "")[:10] or None,
                         "closed_date": (r["closed"] or "")[:10] or None,
                         "_raw_record_id": r["raw_record_id"], "_match_confidence": link["confidence"]})
        return rows

    # -- bookkeeping -----------------------------------------------------------
    def record_match(self, rec, m, input_company=None, input_domain=None, input_email=None, input_country=None,
                     contact_id=None, contact_confidence=None, contact_reason=None) -> None:
        reviewed = m.get("reviewed") or {}
        self.matches.append({
            "raw_record_id": rec["raw_record_id"], "input_company_name": input_company, "input_domain": input_domain,
            "input_email": input_email, "input_country": input_country, "matched_account_id": m["account_id"],
            "match_confidence": m["confidence"], "match_reason": m["reason"], "decision": m["decision"],
            "candidate_account_ids": json.dumps(m["candidates"]) if m.get("candidates") else None,
            "matched_contact_id": contact_id, "contact_match_confidence": contact_confidence,
            "contact_match_reason": contact_reason, "reviewed_by": reviewed.get("decided_by"),
            "reviewed_at": reviewed.get("decided_on"),
            "record_date": rec.get("received_at") or rec.get("occurred_at") or rec.get("created") or rec.get("modified")})

    def save(self, conn: sqlite3.Connection, accounts: list[dict], opportunities: list[dict]) -> None:
        db.insert(conn, "accounts", accounts)
        db.insert(conn, "account_aliases", self.aliases)
        db.insert(conn, "contacts", [{k: v for k, v in c.items() if not k.startswith("_")}
                                     for c in self.contacts.values()])
        db.insert(conn, "identity_matches", self.matches)
        db.insert(conn, "opportunities", [{k: v for k, v in o.items() if not k.startswith("_")} for o in opportunities])
        db.insert(conn, "data_quality_issues", self.issues)
        conn.commit()


def resolve_all(conn: sqlite3.Connection, records: dict[str, list[dict]]) -> dict:
    """Run every matching step in order and save the results. Returns what later steps need."""
    resolver = Resolver()
    accounts = resolver.load_accounts(records["master_list"])
    resolver.resolve_crm_accounts(records["crm_accounts"])
    resolver.load_crm_contacts(records["crm_contacts"])
    opportunities = resolver.resolve_opportunities(records["opportunities"])
    resolver.resolve_activity(records["activity"])
    resolver.save(conn, accounts, opportunities)
    return {"accounts": resolver.accounts, "contacts": resolver.contacts, "opportunities": opportunities,
            "resolver": resolver}
