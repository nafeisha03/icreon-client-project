"""Invents the synthetic scenario: target companies and two years of their activity.

Run it from the project folder:

    python -m calder_abm.generate_data

It writes three folders:

    data/raw/         messy exports, the way real systems deliver them
    data/clean/       the same scenario with no mess (the "clean twin")
    data/answer_key/  the hidden truth: who each record really belongs to, each
                      account's true stage at every review point, and the patterns
                      planted on purpose. The system never reads this folder;
                      only the tests and the evaluation do.

Everything is invented and repeatable: the same seed in config/scenario.yaml
always produces the same files. Nothing here is real Calder Hollis data.

How the scenario is built, in plain terms:
  1. Make the companies and their people.
  2. Decide on purpose which companies attend roundtables, get technology-partner
     referrals and so on (the planted patterns).
  3. Decide where each company ends up (signed, lost, stalled, still open, early),
     giving the planted real effects a real influence on that.
  4. Lay out each company's journey in dated steps.
  5. Turn the journey into dated events, plus everyday marketing noise.
  6. Write the events into fifteen export files, once cleanly and once messily.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import config

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

STEP_STAGE = {
    "A": "Aware", "I": "Interested", "Q": "Qualified Lead", "C": "Active Conversation",
    "P": "Proposal / RFP", "V": "Verbal Commitment", "S": "Signed Agreement",
}
STEPS = ["A", "I", "Q", "C", "P", "V", "S"]
STAGE_STEP = {v: k for k, v in STEP_STAGE.items()}

# Typical days between journey steps (low, high). They add up to about 11 months.
GAPS = {"A": (20, 110), "I": (30, 120), "Q": (10, 50), "C": (40, 110), "P": (30, 90), "V": (15, 50)}

MEANINGFUL_KINDS = {
    "conference_conversation", "event_attended", "email_replied", "personal_introduction",
    "informal_conversation", "tech_partner_referral", "need_identified", "meeting_scheduled",
    "discovery_meeting", "executive_meeting", "follow_up_meeting", "proposal_requested",
    "pricing_requested", "rfp_received", "legal_joined", "procurement_started",
    "verbal_commitment", "contract_negotiation", "contract_signed",
}


def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def iso_z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def plus(day: date, n: int) -> date:
    return day + timedelta(days=n)


def months_between(start: date, end: date):
    """First day of every month from start to end."""
    current = date(start.year, start.month, 1)
    while current <= end:
        yield current
        current = date(current.year + (current.month // 12), current.month % 12 + 1, 1)


def weighted_sample(rng: random.Random, items: list, weight, k: int) -> list:
    """Pick k items without replacement; heavier items are more likely (Efraimidis-Spirakis)."""
    keyed = [(rng.random() ** (1.0 / max(weight(item), 1e-9)), index, item)
             for index, item in enumerate(items)]
    keyed.sort(reverse=True)
    return [item for _, _, item in keyed[:k]]


# ----------------------------------------------------------------------------
# Fixed lists used to invent names
# ----------------------------------------------------------------------------

FIXED_ACCOUNTS = [
    # name, domain, industry, city, region, country -- deliberate identity traps
    ("ABC Corp", "abccorp.com", "Insurance", "Hartford", "CT", "US"),
    ("Meridian Health", "meridianhealth.com", "Healthcare", "Boston", "MA", "US"),
    ("Meridian Health", "meridianhealth.ca", "Healthcare", "Toronto", "ON", "CA"),
    ("Harborline Insurance", "harborlineins.com", "Insurance", "Providence", "RI", "US"),
    ("Summit Financial Group", "summitfg.com", "Banking", "Charlotte", "NC", "US"),
    ("Summit Financial Holdings", "summitfh.com", "Asset management", "Denver", "CO", "US"),
]

PREFIXES = [
    "Ashford", "Bluewater", "Brightwater", "Cedar Ridge", "Clearwater", "Crestview",
    "Eastbrook", "Fairhaven", "Granite", "Ironwood", "Lakeshore", "Linden", "Maplewood",
    "Northgate", "Oakmont", "Parkview", "Pinecrest", "Quarry", "Redstone", "Riverbend",
    "Silverline", "Stonebridge", "Willowbrook", "Yorkfield",
    "Alder", "Briarwood", "Canyon", "Copperfield", "Driftwood", "Elmstead", "Glenmore",
    "Hawthorne", "Juniper", "Kingsley", "Larkspur", "Millbrook", "Norwood", "Orchard",
    "Pembrook", "Quinton", "Rosewood", "Sagebrook", "Thornbury", "Upland", "Wexford",
    "Ambergate", "Bellhaven", "Coldspring", "Dunmore", "Foxhall", "Hollister",
    "Ivywood", "Kestrel", "Marlow", "Newbury", "Oldfield", "Prescott",
    "Ravenwood", "Shoreline", "Tamarack", "Underhill", "Whitmore", "Ashby",
    "Darby", "Easton", "Inwood", "Jameson", "Lowell", "Ogden", "Radcliffe",
    "Upton", "Yardley", "Arden", "Bexley", "Clifton",
    "Fenwick", "Gilford", "Halsey", "Jarvis", "Northam", "Ellery", "Corbin",
    "Waverly", "Holloway", "Ridgeline", "Saltmarsh", "Lyndon",
    "Brantley", "Cobalt", "Dellwood", "Fernhill", "Gresham", "Kilbourne",
    "Merrow", "Netherby", "Oxley", "Penrose", "Rutledge", "Stratton", "Thistle",
    "Ashgrove", "Blackthorn", "Cresthaven", "Eastwick", "Goldcrest",
    "Hartwell", "Islington", "Lindell", "Moorland", "Northcliff", "Oakhurst",
    "Pinehaven", "Quillon", "Redfern", "Southmoor", "Trevelyan", "Vandermeer", "Westbury",
    "Yewdale", "Alcott", "Barrowfield", "Caldwell",
    "Aldenmoor", "Brackenridge", "Corrowin", "Delvane", "Ferrowby", "Halverston", "Ivenbrook",
    "Jorvale", "Lanmoor", "Morvane", "Norcliffe", "Orlavale", "Rivenhall", "Stelmere", "Ulverbrook",
]

INDUSTRIES = {
    "Insurance": [("Insurance", "ins"), ("Mutual", "mutual"), ("Assurance", "assurance")],
    "Banking": [("Bank", "bank"), ("Bancorp", "bancorp"), ("Savings Bank", "savings")],
    "Asset management": [("Capital", "capital"), ("Asset Management", "am"), ("Investments", "invest")],
    "Healthcare": [("Health", "health"), ("Health Systems", "hs"), ("Medical Center", "medical")],
    "Pharmaceuticals": [("Pharmaceuticals", "pharma"), ("Biosciences", "bio"), ("Therapeutics", "tx")],
    "Manufacturing": [("Industries", "ind"), ("Manufacturing", "mfg"), ("Components", "components")],
    "Retail": [("Stores", "stores"), ("Brands", "brands"), ("Retail", "retail")],
    "Technology": [("Software", "software"), ("Systems", "systems"), ("Analytics", "analytics")],
    "Energy and utilities": [("Energy", "energy"), ("Power", "power"), ("Utilities", "utilities")],
    "Transportation and logistics": [("Logistics", "logistics"), ("Freight", "freight"), ("Transport", "transport")],
}

CITIES = [
    ("New York", "NY", "US"), ("Chicago", "IL", "US"), ("Boston", "MA", "US"), ("Hartford", "CT", "US"),
    ("Charlotte", "NC", "US"), ("Atlanta", "GA", "US"), ("Dallas", "TX", "US"), ("Houston", "TX", "US"),
    ("Denver", "CO", "US"), ("Minneapolis", "MN", "US"), ("Columbus", "OH", "US"),
    ("Philadelphia", "PA", "US"), ("Pittsburgh", "PA", "US"), ("Richmond", "VA", "US"),
    ("Baltimore", "MD", "US"), ("Nashville", "TN", "US"), ("Phoenix", "AZ", "US"),
    ("Seattle", "WA", "US"), ("San Francisco", "CA", "US"), ("Los Angeles", "CA", "US"),
    ("St. Louis", "MO", "US"), ("Milwaukee", "WI", "US"), ("Indianapolis", "IN", "US"),
    ("Omaha", "NE", "US"), ("Des Moines", "IA", "US"), ("Washington", "DC", "US"),
    ("Toronto", "ON", "CA"), ("Montreal", "QC", "CA"), ("London", "England", "GB"),
    ("Edinburgh", "Scotland", "GB"),
]

OWNERS = ["R. Okafor", "M. Chen", "S. Patel", "D. Alvarez", "J. Whitfield", "K. Nakamura"]
OWNER_EMAIL = {o: o.replace(". ", ".").lower().replace(" ", "") + "@calderhollis.com" for o in OWNERS}

FIRST_NAMES = [
    "Jane", "Luis", "Priya", "Marcus", "Elena", "David", "Aisha", "Tom", "Grace", "Omar",
    "Hannah", "Wei", "Carlos", "Fatima", "Ben", "Sofia", "Daniel", "Mei", "Ravi", "Laura",
    "Kwame", "Nina", "Peter", "Yuki", "Sam", "Olivia", "Ahmed", "Claire", "Jorge", "Anna",
    "Victor", "Leah", "Ibrahim", "Rachel", "Arjun", "Megan", "Tariq", "Julia", "Kenji", "Paula",
    "Nathan", "Zara", "Owen", "Ines", "Felix", "Chloe", "Diego", "Amara", "Simon", "Lena",
    "Rosa", "Hugo", "Naomi", "Ethan", "Mira", "Caleb", "Tessa", "Andre", "Lucia", "Graham",
]
LAST_NAMES = [
    "Smith", "Ortega", "Raman", "Bell", "Novak", "Kim", "Okonkwo", "Harris", "Liu", "Haddad",
    "Fischer", "Zhang", "Mendes", "Aziz", "Carter", "Rossi", "Murphy", "Tanaka", "Iyer", "Schmidt",
    "Mensah", "Petrov", "Walsh", "Sato", "Brooks", "Nguyen", "Farouk", "Dubois", "Vargas", "Lindqvist",
    "Moreau", "Cohen", "Bello", "Shapiro", "Menon", "Doyle", "Rahman", "Weber", "Mori", "Silva",
    "Grant", "Malik", "Price", "Costa", "Becker", "Laurent", "Ramos", "Eze", "Clarke", "Jansen",
    "Torres", "Keller", "Adeyemi", "Hughes", "Kapoor", "Russo", "Lam", "Ferreira", "Ward", "Ali",
]

ROLE_TITLES = {
    "Influencer": ["Head of Risk", "Director of Model Risk", "Head of Data Governance",
                   "Senior Manager, Compliance", "Director of Risk Analytics"],
    "Decision Maker": ["Chief Audit Executive", "Head of Internal Audit", "Chief Compliance Officer",
                       "VP Risk Management"],
    "Executive Sponsor": ["Chief Risk Officer", "Chief Financial Officer", "Chief Operating Officer"],
    "Legal Reviewer": ["General Counsel", "Deputy General Counsel"],
    "Procurement": ["Director of Procurement", "Vendor Management Lead", "Procurement Manager"],
    "Finance Approver": ["Controller", "VP Finance", "Director of FP&A"],
    "Technical Evaluator": ["Head of Data Science", "Director of AI Engineering", "Chief Data Officer"],
}
OLD_TITLES = {  # what an out-of-date CRM record still says
    "Head of Risk": "Director of Risk", "VP Risk Management": "Director of Risk Management",
    "Chief Risk Officer": "Deputy Chief Risk Officer", "Head of Internal Audit": "Senior Audit Manager",
    "Director of Model Risk": "Model Risk Manager", "General Counsel": "Deputy General Counsel",
}

TECH_PARTNERS = ["Arcwise AI", "Tessellate Cloud", "Brightpath Data", "Corvid Analytics"]
COMPETITORS = ["a Big Four firm", "another regional firm", "a boutique AI consultancy"]
NETWORK_GUESSES = ["Comcast", "Verizon Business", "Spectrum", "Amazon Web Services", "Zscaler"]


# ----------------------------------------------------------------------------
# The objects the scenario is made of
# ----------------------------------------------------------------------------

@dataclass
class Account:
    account_id: str
    name: str
    domain: str
    industry: str
    size: str
    city: str
    region: str
    country: str
    owner: str
    tier: int
    relationship: str = "None"
    # planted flags
    roundtable: bool = False
    referral: str = "none"            # none, accepted, declined
    intro: bool = False
    rfp_led: bool = False
    linkedin: bool = False
    # outcome
    position: str = "Target"
    lost_at: str | None = None
    loss_reason: str | None = None
    tag: str | None = None            # a playbook case or a planted data problem
    journey: str = "None"
    plan: dict = field(default_factory=dict)
    value_usd: int | None = None
    # CRM
    crm_id: str | None = None
    crm_dup_id: str | None = None
    crm_dup_name: str | None = None


@dataclass
class Person:
    person_id: str
    account_id: str
    first: str
    last: str
    title: str
    role: str
    email: str
    in_crm_from: date | None = None
    crm_contact_id: str | None = None
    personal_email: str | None = None
    old_title: str | None = None

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Event:
    event_id: str
    account_id: str | None
    kind: str                      # the signal type it truly is
    when: datetime
    person_id: str | None = None
    campaign_id: str | None = None
    channels: tuple = ()           # where it gets recorded; empty = never recorded
    sentence: str | None = None    # for notes: the sentence a partner wrote
    recognized: bool = True        # False = phrased in a way the notes reader does not know
    extra: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Building the world
# ----------------------------------------------------------------------------

class World:
    def __init__(self, scenario: dict):
        self.sc = scenario
        self.rng = random.Random(scenario["seed"])
        self.launch = date.fromisoformat(scenario["launch_date"])
        self.end = date.fromisoformat(scenario["end_date"])
        self.goal = date.fromisoformat(config.settings()["goal"]["goal_date"])
        self.accounts: list[Account] = []
        self.people: dict[str, list[Person]] = defaultdict(list)
        self.events: list[Event] = []
        self.campaigns = {c["id"]: c for c in scenario["campaigns"]}
        self._event_counter = 0
        self._person_counter = 0
        self._used_names: set[str] = set()
        self.webinars = self._schedule_webinars()
        self.dinners = [date.fromisoformat(d) for d in [
            "2024-12-05", "2025-02-20", "2025-04-24", "2025-06-19", "2025-09-18", "2025-11-13",
            "2026-01-22", "2026-03-19", "2026-05-21", "2026-07-16", "2026-09-10"]]
        self.summits = {"CMP-04": date.fromisoformat("2025-05-14"),
                        "CMP-05": date.fromisoformat("2026-05-13")}
        self.report_launch = date.fromisoformat("2025-02-10")
        self.referral_start = date.fromisoformat("2024-12-05")

    # -- helpers ---------------------------------------------------------------
    def _schedule_webinars(self) -> list[date]:
        day, out = date.fromisoformat("2024-11-14"), []
        while day <= self.end:
            out.append(day)
            day = plus(day, 42)
        return out

    def add(self, account_id, kind, day, *, hour=None, person=None, campaign=None,
            channels=(), sentence=None, recognized=True, **extra) -> Event:
        self._event_counter += 1
        hour = self.rng.randint(9, 17) if hour is None else hour
        event = Event(f"E-{self._event_counter:06d}", account_id, kind,
                      at(day, hour, self.rng.choice([0, 15, 30, 45])),
                      person.person_id if person else None, campaign, tuple(channels),
                      sentence, recognized, dict(extra))
        self.events.append(event)
        if person and channels and set(channels) & {"crm", "calendar"} and person.in_crm_from is None:
            person.in_crm_from = day
        return event

    def person(self, account: Account, role: str) -> Person:
        """The account's person in this role, created the first time it is needed."""
        for p in self.people[account.account_id]:
            if p.role == role:
                return p
        rng = self.rng
        while True:
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            if f"{first} {last}" not in self._used_names:
                self._used_names.add(f"{first} {last}")
                break
        self._person_counter += 1
        title = rng.choice(ROLE_TITLES[role])
        email = f"{first}.{last}@{account.domain}".lower()
        p = Person(f"P-{self._person_counter:04d}", account.account_id, first, last, title, role, email)
        self.people[account.account_id].append(p)
        return p

    def account(self, account_id: str) -> Account:
        return next(a for a in self.accounts if a.account_id == account_id)


def make_accounts(w: World) -> None:
    sc, rng = w.sc, w.rng
    total = sc["accounts"]["total"]
    used_domains = set()
    rows = list(FIXED_ACCOUNTS)
    prefixes = PREFIXES[:]
    rng.shuffle(prefixes)
    industries = list(INDUSTRIES)
    while len(rows) < total:
        prefix = prefixes.pop()
        industry = rng.choice(industries)
        word, code = rng.choice(INDUSTRIES[industry])
        city, region, country = rng.choices(CITIES, weights=[4] * 26 + [1, 1, 1, 1])[0]
        tld = {"US": "com", "CA": "ca", "GB": "co.uk"}[country]
        domain = f"{prefix.lower().replace(' ', '')}{code}.{tld}"
        if domain in used_domains:
            continue
        used_domains.add(domain)
        rows.append((f"{prefix} {word}", domain, industry, city, region, country))

    tiers = []
    for tier, count in sc["accounts"]["tiers"].items():
        tiers += [int(tier)] * count
    rng.shuffle(tiers)
    sizes = {1: ["5,000-9,999", "10,000+"], 2: ["1,000-4,999", "5,000-9,999"], 3: ["500-999", "1,000-4,999"]}
    for index, (name, domain, industry, city, region, country) in enumerate(rows, start=1):
        tier = 1 if index == 1 else tiers[index - 1]
        w.accounts.append(Account(
            account_id=f"ACC-{index:03d}", name=name, domain=domain, industry=industry,
            size=rng.choice(sizes[tier]), city=city, region=region, country=country,
            owner=rng.choice(OWNERS), tier=tier))

    # Existing relationships: current clients of other services, and former clients.
    pool = w.accounts[6:]
    clients = rng.sample(pool, sc["accounts"]["current_clients"])
    for a in clients:
        a.relationship = "Current client, other services"
    rest = [a for a in pool if a.relationship == "None"]
    for a in rng.sample(rest, sc["accounts"]["former_clients"]):
        a.relationship = "Former client"

    # Research list: every target account starts with a champion and a decision maker in the CRM.
    for a in w.accounts:
        for role in ["Influencer", "Decision Maker"] + (["Executive Sponsor"] if a.tier == 1 else []):
            w.person(a, role).in_crm_from = plus(w.launch, -30)


def assign_planted_flags(w: World) -> None:
    sc, rng = w.sc, w.rng
    patterns = sc["planted_patterns"]
    pool = w.accounts[6:]   # the identity-trap accounts are kept out of the planted patterns
    for a in rng.sample(w.accounts, patterns["traps"]["linkedin_ads_change_nothing"]["accounts_shown"]):
        a.linkedin = True
    # Roundtables invite both clients of the firm's other services and new relationships.
    attending = patterns["real_effects"]["roundtables"]["attending_accounts"]
    clients = [a for a in pool if a.relationship == "Current client, other services"]
    others = [a for a in pool if a.relationship != "Current client, other services"]
    from_clients = min(len(clients), round(attending * 0.3))
    for a in rng.sample(clients, from_clients) + rng.sample(others, attending - from_clients):
        a.roundtable = True
    referral_cfg = patterns["real_effects"]["technology_partner_referrals"]
    non_clients = [a for a in pool if a.relationship != "Current client, other services" and not a.roundtable]
    referred = rng.sample(non_clients, referral_cfg["referred_accounts"])
    for a in referred:
        a.referral = "accepted"
    for a in referred[: referral_cfg["never_agreed"]]:
        a.referral = "declined"
    candidates = [a for a in pool if a.referral == "none" and not a.roundtable]
    for a in rng.sample(candidates, sc["partner_introductions"]["accounts"]):
        a.intro = True


def assign_positions(w: World) -> None:
    """Decide where every account ends up. The planted real effects change the odds."""
    sc, rng = w.sc, w.rng
    patterns = sc["planted_patterns"]
    counts = dict(sc["final_positions"])

    def weight(a: Account) -> float:
        value = {1: 1.6, 2: 1.0, 3: 0.6}[a.tier]
        if a.relationship == "Current client, other services":
            value *= patterns["traps"]["current_clients_buy_anyway"]["buying_weight"]
        if a.referral == "declined":
            value *= 0.3
        if a.intro:
            value *= sc["partner_introductions"]["effect_weight"]
        return value

    def planted_effect(a: Account) -> float | None:
        """The chance of moving forward for accounts touched by a planted real effect."""
        if a.roundtable:
            return patterns["real_effects"]["roundtables"]["chance_of_moving_forward"]
        if a.referral == "accepted":
            return patterns["real_effects"]["technology_partner_referrals"]["chance_of_moving_forward"]
        return None

    def head_start(a: Account) -> float | None:
        """Planted effects first; then current clients (who buy anyway) and introductions."""
        effect = planted_effect(a)
        if effect is not None:
            return effect
        if a.relationship == "Current client, other services":
            return patterns["traps"]["current_clients_buy_anyway"]["chance_of_moving_forward"]
        if a.intro:
            return sc["partner_introductions"]["chance_of_moving_forward"]
        return None

    def signing_weight(a: Account) -> float:
        return weight(a) * (3.0 if planted_effect(a) else 1.0)

    # The identity-trap accounts get fixed parts to play.
    fixed = {
        "ACC-001": ("Proposal / RFP", "A5"),       # ABC Corp, the design document's example
        "ACC-002": ("Active Conversation", "A10"),
        "ACC-003": ("Aware", None),
        "ACC-004": ("Interested", None),
        "ACC-005": ("Signed Agreement", None),
        "ACC-006": ("Aware", None),
    }
    for account_id, (position, tag) in fixed.items():
        a = w.account(account_id)
        a.position, a.tag = position, tag
        counts[position] -= 1

    pool = [a for a in w.accounts if a.account_id not in fixed]
    pipeline_names = ["Signed Agreement", "Lost", "Verbal Commitment", "Proposal / RFP",
                      "Active Conversation", "Qualified Lead", "Stalled"]
    pipeline_total = sum(counts[name] for name in pipeline_names)
    # Accounts with a head start (planted effects, current clients, introductions) each get
    # their own chance of moving forward; the remaining places go to everyone else.
    started = [a for a in pool if head_start(a) is not None and rng.random() < head_start(a)]
    rng.shuffle(started)
    started.sort(key=lambda a: planted_effect(a) is None)   # planted effects keep their places first
    pipeline = started[:pipeline_total]
    others = [a for a in pool if head_start(a) is None]
    pipeline += weighted_sample(rng, others, weight, pipeline_total - len(pipeline))
    # Signing works the same way: planted effects and current clients get a better chance.
    def signing_chance(a: Account) -> float:
        return 0.4 if (planted_effect(a) or a.relationship == "Current client, other services") else 0.15

    signed = [a for a in pipeline if rng.random() < signing_chance(a)]
    rng.shuffle(signed)
    signed = signed[:counts["Signed Agreement"]]
    signed += weighted_sample(rng, [a for a in pipeline if a not in signed], signing_weight,
                              counts["Signed Agreement"] - len(signed))
    rest = [a for a in pipeline if a not in signed]
    lost = weighted_sample(rng, rest, lambda a: signing_weight(a) ** -0.5, counts["Lost"])
    rest = [a for a in rest if a not in lost]
    rng.shuffle(rest)
    for a in signed:
        a.position = "Signed Agreement"
    lost_stages = []
    for stage, n in sc["lost_at"].items():
        lost_stages += [stage] * n
    rng.shuffle(lost_stages)
    for a, stage in zip(lost, lost_stages):
        a.position, a.lost_at = "Lost", stage
    cursor = 0
    for name in ["Verbal Commitment", "Proposal / RFP", "Active Conversation", "Qualified Lead", "Stalled"]:
        for a in rest[cursor: cursor + counts[name]]:
            a.position = name
        cursor += counts[name]

    # Accounts outside the pipeline. Anyone who attended a roundtable, accepted a
    # referral or an introduction has at least taken a deliberate step.
    outside = [a for a in pool if a not in pipeline]
    forced = [a for a in outside if a.roundtable or a.referral == "accepted" or a.intro]
    n_interested = max(counts["Interested"], len(forced))
    others = [a for a in outside if a not in forced]
    interested = forced + weighted_sample(rng, others, weight, n_interested - len(forced))
    others = [a for a in others if a not in interested]
    n_aware = counts["Aware"] - (n_interested - counts["Interested"])
    aware = weighted_sample(rng, others, weight, n_aware)
    for a in interested:
        a.position = "Interested"
    for a in aware:
        a.position = "Aware"
    for a in others:
        if a not in aware:
            a.position = "Target"

    # Loss reasons, by how far the deal got.
    reasons = {"Active Conversation": ["No decision", "Chose another firm", "Priorities changed"],
               "Proposal / RFP": ["Chose another firm"] * 4 + ["Budget cut or freeze", "Priorities changed"],
               "Verbal Commitment": ["Budget cut or freeze", "Leadership change", "Procurement stalled"]}
    for stage in reasons:
        deals = [a for a in w.accounts if a.lost_at == stage]
        for a, reason in zip(deals, reasons[stage] * 3):
            a.loss_reason = reason

    # Journeys that start with a formal RFP and skip the meetings.
    rfp_pool = [a for a in w.accounts if not (a.roundtable or a.intro or a.referral != "none")
                and a.account_id not in fixed
                and (a.position in {"Signed Agreement", "Verbal Commitment"}
                     or a.lost_at in {"Proposal / RFP", "Verbal Commitment"})]
    for a in rng.sample(rfp_pool, min(sc["rfp_led_accounts"], len(rfp_pool))):
        a.rfp_led = True

    # Playbook cases among the open deals, so every rule has an example.
    def tag_accounts(position: str, tags: list[str]) -> None:
        group = [a for a in w.accounts if a.position == position and a.tag is None
                 and a.account_id not in fixed and not a.rfp_led]
        for a, tag in zip(group, tags):
            a.tag = tag

    tag_accounts("Proposal / RFP", ["A7", "A8", "A6", "on_track"])
    tag_accounts("Verbal Commitment", ["A4", "A4_warning", "on_track"])
    tag_accounts("Active Conversation", ["A10", "A11", "A11", "on_track", "on_track"])
    tag_accounts("Qualified Lead", ["probable_only", "probable_only", "A12", "A12", "A12"])
    tag_accounts("Stalled", ["stale_crm_verbal", "stale_crm_proposal"])
    tag_accounts("Interested", ["A13"])

    # Deal values.
    value_cfg = sc["deal_value_usd"]
    for a in w.accounts:
        if a.position in pipeline_names:
            value = rng.gauss(value_cfg["mean"], 90000)
            a.value_usd = int(round(min(max(value, value_cfg["low"]), value_cfg["high"]) / 5000) * 5000)


def plan_journeys(w: World) -> None:
    """Lay out each account's journey as dated steps (A=Aware ... S=Signed)."""
    rng, end, launch = w.rng, w.end, w.launch
    signed = [a for a in w.accounts if a.position == "Signed Agreement"]
    rng.shuffle(signed)
    early = w.sc["signed_by_goal_date"]
    for index, a in enumerate(signed):
        if index < early:
            a.plan["S"] = plus(date.fromisoformat("2025-07-01"), rng.randint(0, (w.goal - date(2025, 7, 1)).days - 7))
        else:
            a.plan["S"] = plus(w.goal, rng.randint(7, (end - w.goal).days - 5))

    for a in w.accounts:
        pos = a.position
        if pos == "Signed Agreement":
            back_plan(w, a, "S", a.plan["S"])
        elif pos == "Lost":
            lost_day = plus(date.fromisoformat("2025-04-15"), rng.randint(0, 510))
            lost_day = min(lost_day, plus(end, -10))
            step = STAGE_STEP[a.lost_at]
            back_plan(w, a, step, plus(lost_day, -rng.randint(15, 60)))
            a.plan["L"] = lost_day
        elif pos in {"Verbal Commitment", "Proposal / RFP", "Active Conversation", "Qualified Lead"}:
            offsets = {"A7": 12, "A8": 35, "A5": 40, "A6": 30, "on_track": 18, "A4": 20, "A4_warning": 25,
                       "A10": 12, "A11": 26, "A12": rng.randint(5, 30), "probable_only": rng.randint(10, 35)}
            back_plan(w, a, STAGE_STEP[pos], plus(end, -offsets.get(a.tag, rng.randint(6, 35))))
        elif pos == "Stalled":
            stall_stage = rng.choice(["Q", "C", "P"])
            if a.tag == "stale_crm_verbal":
                stall_stage = "P"
            if a.tag == "stale_crm_proposal":
                stall_stage = "C"
            anchor = plus(date.fromisoformat("2025-02-01"), rng.randint(0, 480))
            back_plan(w, a, stall_stage, anchor)
            a.plan["last"] = plus(anchor, rng.randint(0, 20))
            a.plan["stall_stage"] = stall_stage
        elif pos == "Interested":
            if a.tag == "A13":
                a.plan["A"] = plus(end, -rng.randint(60, 120))
                a.plan["I"] = plus(end, -20)
            else:
                a.plan["A"] = plus(launch, rng.randint(10, (end - launch).days - 120))
                a.plan["I"] = min(plus(a.plan["A"], rng.randint(20, 110)), plus(end, -5))
        elif pos == "Aware":
            a.plan["A"] = plus(launch, rng.randint(10, (end - launch).days - 5))
        # Target: no steps at all.

    # Journey types, and snapping the "Interested" step to a real scheduled event.
    for a in w.accounts:
        p = a.plan
        if not p:
            a.journey = "None"
            continue
        if a.referral == "accepted":
            a.journey = "Technology partner referral"
        elif a.rfp_led:
            a.journey = "RFP-led"
        elif a.roundtable:
            a.journey = "Executive roundtable"
        elif a.intro:
            a.journey = "Partner introduction"
        elif a.relationship == "Current client, other services":
            a.journey = "Conference and existing relationship"
        else:
            a.journey = "Marketing-led"
        if a.roundtable and "I" in p:
            if "Q" in p:
                # The dinner comes before the need is identified: the planted effect works forward in time.
                options = [d for d in w.dinners if d < plus(p["Q"], -7)]
                dinner = options[-1] if options else None
            else:
                # Attendees who never move forward come to a dinner at any point.
                options = [d for d in w.dinners if d >= plus(p["A"], -60)]
                dinner = rng.choice(options) if options else None
            if dinner:
                p["I"] = dinner
                if p["A"] > dinner:
                    p["A"] = dinner
        if a.journey == "Conference and existing relationship" and "I" in p and "Q" in p:
            for campaign_id, summit in w.summits.items():
                if p["A"] <= summit < p["Q"]:
                    p["I"] = summit
                    p["summit"] = campaign_id
                    break


def back_plan(w: World, a: Account, last_step: str, anchor: date) -> None:
    """Work backwards from the last step reached, using typical gaps between steps."""
    rng = w.rng
    if a.rfp_led:
        steps = ["A", "P", "V", "S"]
        steps = steps[: steps.index(last_step) + 1] if last_step in steps else ["A", "P"]
        gaps = {"A": (15, 75), "P": GAPS["P"], "V": GAPS["V"]}
    elif a.referral == "accepted":
        steps = ["Q", "C", "P", "V", "S"]
        steps = steps[: steps.index(last_step) + 1] if last_step in steps else ["Q"]
        gaps = GAPS
    else:
        steps = STEPS[: STEPS.index(last_step) + 1]
        gaps = GAPS
    if a.referral == "accepted":
        earliest = w.referral_start
    elif a.roundtable:
        # Roundtable attendees need a dinner at least a week before their need is identified.
        earliest = plus(w.dinners[0], 8)
    else:
        earliest = plus(w.launch, 7)
    durations = [rng.randint(*gaps[steps[i]]) for i in range(len(steps) - 1)]
    room = (anchor - earliest).days
    if sum(durations) > room:
        scale = max(room, 3 * len(durations)) / max(sum(durations), 1)
        durations = [max(3, int(d * scale)) for d in durations]
    day = anchor
    a.plan[steps[-1]] = day
    for step, gap in zip(reversed(steps[:-1]), reversed(durations)):
        day = plus(day, -gap)
        a.plan[step] = max(day, earliest)
    if a.referral == "accepted":
        a.plan["A"] = a.plan["I"] = a.plan["Q"]


# ----------------------------------------------------------------------------
# Turning journeys into events
# ----------------------------------------------------------------------------

NEED_SENTENCES = [
    ("{first} said the board wants an AI controls review before year end.", True),
    ("They need an independent review of their model risk controls.", True),
    ("{first} asked us to assess their AI governance framework.", True),
    ("The regulator asked them for an AI inventory and they need help with the controls.", True),
    ("{first} is worried about their AI controls and wants a second pair of eyes.", False),
]
BUDGET_SENTENCES = [("Budget is approved for this year.", True), ("{first} confirmed funding is in place.", True),
                    ("The CFO has earmarked funds for this.", False)]
TIMELINE_SENTENCES = [("They want to decide by {month}.", True), ("They aim to start in {month}.", True),
                      ("Hoping to kick off once the quarter closes.", False)]
COMPETITOR_SENTENCES = [("They are also talking to {competitor}.", True),
                        ("There is a competing proposal from {competitor}.", True),
                        ("{competitor} is in the mix too.", False)]
DECISION_SENTENCES = [("The CRO signs off on anything above $250K.", True),
                      ("The decision sits with the audit committee.", True)]
HEDGE_SENTENCES = ["They may have budget next year.", "{first} might bring in their general counsel.",
                   "Possibly interested in a pilot first.", "Not sure whether procurement needs to be involved."]
LOSS_SENTENCES = {
    "Chose another firm": "They chose another firm for the assessment.",
    "No decision": "{first} told us they decided not to proceed this year.",
    "Priorities changed": "They paused the project and told us they are not going ahead for now.",
    "Budget cut or freeze": "{first} told us they decided not to proceed after the budget was cut.",
    "Leadership change": "The new CRO decided not to proceed with the review.",
    "Procurement stalled": "After months in procurement they decided not to proceed.",
}
WARNING_SENTENCES = {
    "budget_cut": "Their budget was cut after a weak quarter.",
    "leadership_change": "They have a new CRO starting next month and our sponsor has moved to a new role.",
    "procurement_delay": "We are stuck in procurement; vendor approval delayed again.",
    "priorities_changed": "They paused the project to focus on a system migration.",
}


def fill(template: str, person: Person | None = None, **kw) -> str:
    return template.format(first=person.first if person else "They", **kw)


def journey_events(w: World, a: Account) -> None:
    """Dated events for one account's journey, from first contact to its final position."""
    rng, p = w.rng, a.plan
    if not p:
        return
    champion = w.person(a, "Influencer")
    decider = w.person(a, "Decision Maker")
    sponsor = w.person(a, "Executive Sponsor")
    owner = a.owner

    # A: the first engagement.
    if "A" in p and a.journey != "Technology partner referral":
        day = p["A"]
        if a.roundtable and p.get("I") == day:
            pass  # the roundtable itself is the first engagement
        elif a.rfp_led or rng.random() < 0.3:
            w.add(a.account_id, "website_visit", day, campaign="CMP-10", channels=("web",), page="/insights/ai-controls")
        elif a.linkedin and rng.random() < 0.1:
            w.add(a.account_id, "linkedin_engagement", day, campaign="CMP-07", channels=("linkedin",))
        else:
            w.add(a.account_id, "outreach_sent", day, hour=8, person=champion, campaign="CMP-08", channels=("email",))
            w.add(a.account_id, "email_opened", day, hour=11, person=champion, campaign="CMP-08", channels=("email",))

    # I: the first deliberate step.
    if "I" in p and a.journey != "Technology partner referral":
        day = p["I"]
        if a.journey == "Executive roundtable" and day in w.dinners:
            w.add(a.account_id, "event_attended", day, hour=19, person=champion, campaign="CMP-03",
                  channels=("events",), event_name="Executive Roundtable: AI Controls", event_type="Roundtable")
            if rng.random() < 0.4:
                w.add(a.account_id, "informal_conversation", day, hour=21, person=champion, channels=("notes",),
                      sentence=f"Caught up over dinner with {champion.first} at the roundtable; AI risk is on their board's agenda.")
        elif a.journey == "Partner introduction":
            introducer = rng.choice(["a former colleague", "our audit partner", "a board member we know"])
            w.add(a.account_id, "personal_introduction", day, person=champion, campaign="CMP-09",
                  channels=("crm", "notes"), activity="Introduction",
                  sentence=f"{introducer[0].upper() + introducer[1:]} introduced us to {champion.name} at {a.name}.")
        elif a.journey == "Conference and existing relationship":
            if p.get("summit"):
                w.add(a.account_id, "conference_conversation", day, person=champion, campaign=p["summit"],
                      channels=("events", "notes"), event_name=w.campaigns[p["summit"]]["name"],
                      event_type="Conference",
                      sentence=f"Met {champion.first} at the summit; they stopped by our booth to ask about AI audits.")
            else:
                recorded = ("crm", "notes") if rng.random() < 0.85 else ()
                w.add(a.account_id, "informal_conversation", day, person=sponsor, channels=recorded,
                      activity="Call", sentence=f"Coffee with {sponsor.first} after the audit committee meeting; asked how we handle AI.")
        elif a.journey == "RFP-led":
            pass
        else:
            choice = rng.random()
            webinars = [d for d in w.webinars if p["A"] <= d <= day]
            if choice < 0.4 and webinars:
                when = webinars[-1]
                p["I"] = when
                w.add(a.account_id, "webinar_registered", plus(when, -rng.randint(2, 9)), person=champion,
                      campaign="CMP-01", channels=("events",), event_name="AI Controls Webinar", event_type="Webinar")
                w.add(a.account_id, "webinar_attended", when, hour=16, person=champion, campaign="CMP-01",
                      channels=("events",), event_name="AI Controls Webinar", event_type="Webinar")
            elif choice < 0.65:
                campaign = "CMP-02" if day >= w.report_launch else "CMP-10"
                asset = "State of AI Governance Report" if campaign == "CMP-02" else "AI Controls Checklist"
                w.add(a.account_id, "content_downloaded", day, person=champion, campaign=campaign,
                      channels=("web",), asset=asset, page="/resources")
            else:
                w.add(a.account_id, "outreach_sent", plus(day, -2), hour=8, person=champion, campaign="CMP-08",
                      channels=("email",))
                w.add(a.account_id, "email_replied", day, person=champion, campaign="CMP-08", channels=("email",),
                      reply_text="Thanks, this is timely. Happy to compare notes on AI governance.")

    # A referral that never turned into a stated need: the partner introduced them, nothing more.
    if a.journey == "Technology partner referral" and "Q" not in p and "I" in p:
        w.add(a.account_id, "tech_partner_referral", p["I"], person=champion, campaign="CMP-06",
              channels=("referrals",), partner=rng.choice(TECH_PARTNERS), stated_need="", consent="Y")

    # Q: a business need is identified.
    if "Q" in p:
        day = p["Q"]
        if a.journey == "Technology partner referral":
            need = rng.choice([s for s, ok in NEED_SENTENCES if ok])
            w.add(a.account_id, "tech_partner_referral", day, person=champion, campaign="CMP-06",
                  channels=("referrals",), partner=rng.choice(TECH_PARTNERS),
                  stated_need=fill(need, champion), consent="Y")
            w.add(a.account_id, "need_identified", day, person=champion, campaign="CMP-06", channels=(),
                  via="referral")
        else:
            roll = rng.random()
            sentence, recognized = rng.choice(NEED_SENTENCES)
            if a.tag == "probable_only":   # the planted problem here is the matching, not the wording
                sentence, recognized = NEED_SENTENCES[0]
            if a.tag == "probable_only" or roll < 0.62:
                w.add(a.account_id, "need_identified", day, person=champion, channels=("notes",),
                      sentence=fill(sentence, champion), recognized=recognized)
            elif roll < 0.85:
                w.add(a.account_id, "need_identified", day, person=champion, channels=("email",),
                      sentence="We need help with our AI model controls ahead of the regulator's review.",
                      reply_text="We need help with our AI model controls ahead of the regulator's review.")
            else:
                w.add(a.account_id, "need_identified", day, person=champion, channels=())  # never written down

    # C: the first substantive meeting.
    if "C" in p:
        day = p["C"]
        kind = "executive_meeting" if a.relationship == "Current client, other services" else "discovery_meeting"
        attendees = [champion]
        if a.tag not in {"A10"} and rng.random() < 0.55:
            attendees.append(decider)
        if kind == "executive_meeting" and a.tag != "A10":
            attendees.append(sponsor)
        meeting(w, a, kind, day, attendees)
        note_facts(w, a, day, champion, stage="C")

    # P: proposal / RFP.
    if "P" in p:
        day = p["P"]
        asker = decider if (a.tag != "A10" and rng.random() < 0.5) else champion
        if a.rfp_led:
            w.add(a.account_id, "rfp_received", day, person=asker, channels=("crm",), activity="RFP received",
                  subject=f"RFP received from {a.name}: AI controls assessment")
        else:
            kind = "pricing_requested" if rng.random() < 0.2 else "proposal_requested"
            activity = "Pricing requested" if kind == "pricing_requested" else "Proposal requested"
            channels = ("crm", "notes") if rng.random() < 0.5 else ("crm",)
            sentence = (f"{asker.first} asked for pricing for a phased approach." if kind == "pricing_requested"
                        else f"{asker.first} asked for a proposal for the AI governance assessment.")
            w.add(a.account_id, kind, day, person=asker, channels=channels, activity=activity, sentence=sentence)
        if a.tag != "A7":
            sent_day = plus(day, 13 if a.tag == "A8" else rng.randint(3, 10))
            if sent_day <= w.end:
                w.add(a.account_id, "proposal_sent", sent_day, person=asker, channels=("crm",),
                      activity="Proposal sent", owner=owner)
        legal = a.tag == "A5" or (a.tag not in {"A7", "A8", "A6", "on_track", "A4", "A4_warning"}
                                  and rng.random() < 0.5)
        if legal:
            legal_day = plus(w.end, -17) if a.tag == "A5" else plus(day, rng.randint(10, 40))
            limit = min(p.get("V", w.end), p.get("L", w.end), w.end)
            if legal_day <= limit:
                counsel = w.person(a, "Legal Reviewer")
                if a.account_id == "ACC-001":   # the worked example: the meeting and the partner's note
                    meeting(w, a, "legal_joined", legal_day, [champion, counsel], category="Follow-up")
                    w.add(a.account_id, "legal_joined", legal_day, hour=18, person=counsel, channels=("notes",),
                          sentence="Their general counsel joined the call and legal is reviewing the draft terms.")
                elif rng.random() < 0.6:
                    meeting(w, a, "legal_joined", legal_day, [champion, counsel], category="Follow-up")
                else:
                    w.add(a.account_id, "legal_joined", legal_day, person=counsel, channels=("crm", "notes"),
                          activity="Legal review",
                          sentence="Their general counsel joined the call and legal is reviewing the draft terms.")
        procurement = a.tag == "A6" or (a.tag not in {"A7", "A8", "A5", "on_track", "A4", "A4_warning"}
                                        and rng.random() < 0.4)
        if procurement:
            proc_day = plus(w.end, -13) if a.tag == "A6" else plus(day, rng.randint(15, 50))
            limit = min(p.get("V", w.end), p.get("S", w.end), p.get("L", w.end), w.end)
            if proc_day <= limit:
                buyer = w.person(a, "Procurement")
                w.add(a.account_id, "procurement_started", proc_day, person=buyer,
                      channels=("crm",) if rng.random() < 0.6 else ("notes",), activity="Procurement started",
                      sentence="Procurement has started; they sent us their vendor forms and a security questionnaire.")
        note_facts(w, a, plus(day, 2), asker, stage="P")

    # V: verbal commitment.
    if "V" in p:
        day = p["V"]
        speaker = sponsor if rng.random() < 0.5 else decider
        roll = rng.random()
        channels = ("crm", "notes") if roll < 0.45 else ("notes",) if roll < 0.75 else ("crm",) if roll < 0.92 else ()
        w.add(a.account_id, "verbal_commitment", day, person=speaker, channels=channels, activity="Verbal commitment",
              sentence=rng.choice([f"{speaker.first} confirmed they want to proceed with us.",
                                   f"Verbal yes from {speaker.first} today.",
                                   "They agreed to proceed, pending contract."]))
        if a.tag == "A4_warning":
            w.add(a.account_id, "procurement_delay", plus(w.end, -9), person=champion, channels=("notes",),
                  sentence=WARNING_SENTENCES["procurement_delay"])
        negotiation_day = None
        if "S" in p:
            negotiation_day = plus(day, max(3, (p["S"] - day).days // 2))
        elif a.tag == "on_track":
            negotiation_day = plus(w.end, -3)
        if negotiation_day:
            counsel = w.person(a, "Legal Reviewer")
            w.add(a.account_id, "contract_negotiation", negotiation_day, person=counsel,
                  channels=("crm",) if rng.random() < 0.7 else ("notes",), activity="Negotiation",
                  sentence="Their counsel sent redlines on the engagement letter.")

    # S: signed.
    if "S" in p:
        w.add(a.account_id, "contract_signed", p["S"], person=sponsor, channels=("crm",), activity="Contract signed")

    # Lost, with the warning signs that came first.
    if "L" in p:
        lost_day = p["L"]
        warning = {"Budget cut or freeze": "budget_cut", "Leadership change": "leadership_change",
                   "Procurement stalled": "procurement_delay", "Priorities changed": "priorities_changed"}.get(a.loss_reason)
        if warning:
            w.add(a.account_id, warning, plus(lost_day, -rng.randint(14, 40)), person=champion,
                  channels=("notes",), sentence=WARNING_SENTENCES[warning])
        if a.loss_reason == "Chose another firm" and rng.random() < 0.7:
            w.add(a.account_id, "competitor_mentioned", plus(lost_day, -rng.randint(20, 50)), person=champion,
                  channels=("notes",), sentence=fill(COMPETITOR_SENTENCES[0][0], competitor=rng.choice(COMPETITORS)))
        w.add(a.account_id, "opportunity_lost", lost_day, person=decider,
              channels=("opportunity", "notes") if rng.random() < 0.7 else ("opportunity",),
              sentence=fill(LOSS_SENTENCES[a.loss_reason], decider))

    # Keep active deals from going quiet by accident: a call or meeting at least every 38 days.
    fill_quiet_gaps(w, a, champion)

    # Stalled deals: after the last real contact, only our own chasing and a cancellation.
    if a.position == "Stalled":
        last = p["last"]
        if last > p[p["stall_stage"]]:
            if p["stall_stage"] == "Q":
                w.add(a.account_id, "informal_conversation", last, person=champion, channels=("crm",),
                      activity="Call", subject=f"Call with {champion.name}")
            else:
                meeting(w, a, "follow_up_meeting", last, [champion])
        if rng.random() < 0.5:
            meeting(w, a, "meeting_cancelled", plus(last, rng.randint(15, 30)), [champion], status="cancelled")
        chase = plus(last, 50)
        while chase < w.end:
            w.add(a.account_id, "outreach_sent", chase, person=champion, channels=("crm",), activity="Outreach")
            chase = plus(chase, rng.randint(35, 60))


def meeting(w: World, a: Account, kind: str, day: date, attendees: list[Person], *,
            category: str | None = None, status: str = "held") -> None:
    """A calendar meeting; in the messy data the CRM often logs it a second time."""
    category = category or {"discovery_meeting": "Discovery", "executive_meeting": "Executive",
                            "follow_up_meeting": "Follow-up", "legal_joined": "Follow-up",
                            "meeting_cancelled": "Follow-up"}[kind]
    activity = {"Discovery": "Discovery meeting", "Executive": "Executive meeting",
                "Follow-up": "Follow-up meeting"}[category]
    if kind == "meeting_cancelled":
        activity = "Meeting cancelled"
    first = attendees[0]
    event = w.add(a.account_id, kind, day, person=first, channels=("calendar",), category=category,
                  status=status, attendees=[p.person_id for p in attendees], activity=activity,
                  subject=f"{a.name} / Calder Hollis: {category.lower()} call")
    for person in attendees:
        if person.in_crm_from is None:
            person.in_crm_from = day
    return event


def note_facts(w: World, a: Account, day: date, person: Person, stage: str) -> None:
    """Facts a partner writes down after a meeting: some as known phrases, some not, some hedged."""
    rng = w.rng
    if day > w.end:
        return
    month = (day + timedelta(days=rng.randint(60, 150))).strftime("%B")
    if stage == "C":
        if rng.random() < 0.4:
            s, ok = rng.choice(TIMELINE_SENTENCES)
            w.add(a.account_id, "timeline_confirmed", day, hour=18, person=person, channels=("notes",),
                  sentence=fill(s, person, month=month), recognized=ok)
        if rng.random() < 0.3:
            s, ok = rng.choice(DECISION_SENTENCES)
            w.add(a.account_id, "decision_process_confirmed", day, hour=18, person=person, channels=("notes",),
                  sentence=s, recognized=ok)
    if stage == "P":
        if rng.random() < 0.45:
            s, ok = rng.choice(BUDGET_SENTENCES)
            w.add(a.account_id, "budget_confirmed", day, hour=18, person=person, channels=("notes",),
                  sentence=fill(s, person), recognized=ok)
        roll = rng.random()
        if roll < 0.35:
            s, ok = rng.choice(COMPETITOR_SENTENCES)
            w.add(a.account_id, "competitor_mentioned", day, hour=18, person=person, channels=("notes",),
                  sentence=fill(s, person, competitor=rng.choice(COMPETITORS)), recognized=ok)
        elif roll < 0.45:
            w.add(a.account_id, "no_competitor_confirmed", day, hour=18, person=person, channels=("notes",),
                  sentence=f"{person.first} confirmed they are not talking to anyone else.")
    if rng.random() < 0.25:
        w.add(a.account_id, "hedge", day, hour=18, person=person, channels=("notes",),
              sentence=fill(rng.choice(HEDGE_SENTENCES), person))


def fill_quiet_gaps(w: World, a: Account, champion: Person) -> None:
    """Add calls or follow-up meetings so an active deal never goes 38+ days without contact."""
    p = a.plan
    if "Q" not in p and "P" not in p:
        return
    start = p.get("Q") or p.get("P")
    stop = p.get("S") or p.get("L") or p.get("last") or w.end
    quiet_tail = {"A11": 26, "A8": 30, "A4": 20, "A4_warning": 25, "A5": 20, "A6": 15, "A7": 12, "A10": 12}
    if a.tag in quiet_tail:
        stop = min(stop, plus(w.end, -quiet_tail[a.tag]))
    meaningful = sorted(e.when.date() for e in w.events
                        if e.account_id == a.account_id and e.kind in MEANINGFUL_KINDS)
    meaningful = [d for d in meaningful if start <= d <= stop] or [start]
    marks = sorted(set([start] + meaningful + [stop]))
    for left, right in zip(marks, marks[1:]):
        gap = (right - left).days
        while gap > 38:
            left = plus(left, w.rng.randint(24, 34))
            if left >= right:
                break
            if p.get("C") and left > p["C"]:
                meeting(w, a, "follow_up_meeting", left, [champion])
            else:
                w.add(a.account_id, "informal_conversation", left, person=champion, channels=("crm",),
                      activity="Call", subject=f"Call with {champion.name}")
            gap = (right - left).days


def marketing_events(w: World) -> None:
    """Everyday marketing activity: emails, webinars, ads, roundtables, summits, visits, triggers."""
    rng = w.rng
    patterns = w.sc["planted_patterns"]

    def engaged_from(a: Account) -> date | None:
        return a.plan.get("A")

    def interested_from(a: Account) -> date | None:
        return a.plan.get("I") or a.plan.get("Q") or a.plan.get("P")

    closed_on = {a.account_id: a.plan.get("S") or a.plan.get("L") for a in w.accounts}

    # Email nurture: a send every two months to everyone in the CRM; opens only after first engagement.
    unsubscribed: dict[str, date] = {}
    people = [(a, p) for a in w.accounts for p in w.people[a.account_id] if p.in_crm_from]
    for a, p in rng.sample(people, 15):
        p_day = plus(w.launch, rng.randint(40, 680))
        w.add(a.account_id, "unsubscribed", p_day, person=p, campaign="CMP-08", channels=("email",))
        unsubscribed[p.person_id] = p_day
    for month in months_between(w.launch, w.end):
        if month.month % 2:
            continue
        send_day = plus(month, 9)
        if send_day > w.end:
            continue
        for a in w.accounts:
            for person in w.people[a.account_id]:
                if person.in_crm_from is None or person.in_crm_from > send_day:
                    continue
                if person.person_id in unsubscribed and unsubscribed[person.person_id] <= send_day:
                    continue
                w.add(a.account_id, "outreach_sent", send_day, hour=8, person=person, campaign="CMP-08",
                      channels=("email",))
                start = engaged_from(a)
                if start and send_day >= start and rng.random() < 0.3:
                    w.add(a.account_id, "email_opened", plus(send_day, rng.randint(0, 3)), person=person,
                          campaign="CMP-08", channels=("email",))

    # Webinars: current clients register and attend more often (the trap). Webinars change nothing.
    multiplier = patterns["traps"]["current_clients_buy_anyway"]["webinar_attendance_multiplier"]
    for webinar in w.webinars:
        for a in w.accounts:
            start = engaged_from(a)
            if not start or start > plus(webinar, -10) or a.position == "Target":
                continue
            chance = 0.09 * (multiplier if a.relationship == "Current client, other services" else 1.0)
            if rng.random() >= chance:
                continue
            person = rng.choice(w.people[a.account_id])
            w.add(a.account_id, "webinar_registered", plus(webinar, -rng.randint(2, 9)), person=person,
                  campaign="CMP-01", channels=("events",), event_name="AI Controls Webinar", event_type="Webinar")
            can_attend = interested_from(a) and interested_from(a) <= webinar
            if can_attend and rng.random() < 0.65:
                w.add(a.account_id, "webinar_attended", webinar, hour=16, person=person, campaign="CMP-01",
                      channels=("events",), event_name="AI Controls Webinar", event_type="Webinar")
            else:
                w.add(a.account_id, "webinar_no_show", webinar, hour=16, person=person, campaign="CMP-01",
                      channels=("events",), event_name="AI Controls Webinar", event_type="Webinar")

    # The A13 case: three people from one account engage within a month, no meeting yet.
    for a in w.accounts:
        if a.tag == "A13":
            for offset, role in zip([18, 12, 6], ["Influencer", "Technical Evaluator", "Decision Maker"]):
                person = w.person(a, role)
                day = plus(w.end, -offset)
                w.add(a.account_id, "content_downloaded", day, person=person, campaign="CMP-02",
                      channels=("web",), asset="State of AI Governance Report", page="/resources")

    # The report trap: mostly downloaded AFTER the first meeting, by deals already moving.
    share = patterns["traps"]["report_after_progress"]["share_of_pipeline_accounts_downloading"]
    for a in w.accounts:
        c = a.plan.get("C")
        if not c:
            continue
        stop = min(closed_on[a.account_id] or w.end, a.plan.get("last") or w.end, w.end)
        start = max(plus(c, 5), w.report_launch)
        if start < stop and rng.random() < share:
            day = plus(start, rng.randint(0, max(0, min(120, (stop - start).days))))
            w.add(a.account_id, "content_downloaded", day, person=w.person(a, "Influencer"), campaign="CMP-02",
                  channels=("web",), asset="State of AI Governance Report", page="/resources")
    early = [a for a in w.accounts if a.plan.get("I") and a.plan["I"] >= w.report_launch
             and a.journey == "Marketing-led"]
    for a in rng.sample(early, min(patterns["traps"]["report_after_progress"]["early_downloads"], len(early))):
        w.add(a.account_id, "content_downloaded", a.plan["I"], person=w.person(a, "Influencer"),
              campaign="CMP-02", channels=("web",), asset="State of AI Governance Report", page="/resources")

    # Roundtables: planted attendees sometimes come back for a second dinner.
    for a in w.accounts:
        if not a.roundtable or "I" not in a.plan:
            continue
        later = [d for d in w.dinners if d > a.plan["I"] and d <= (closed_on[a.account_id] or w.end)]
        if later and rng.random() < 0.35:
            w.add(a.account_id, "event_attended", rng.choice(later), hour=19, person=w.person(a, "Decision Maker"),
                  campaign="CMP-03", channels=("events",), event_name="Executive Roundtable: AI Controls",
                  event_type="Roundtable")

    # Summits: booth conversations with accounts that are already interested. No planted effect.
    for campaign_id, summit in w.summits.items():
        crowd = [a for a in w.accounts if interested_from(a) and interested_from(a) <= summit
                 and a.plan.get("summit") != campaign_id and (closed_on[a.account_id] or w.end) >= summit]
        for a in rng.sample(crowd, min(14, len(crowd))):
            w.add(a.account_id, "conference_conversation", summit, person=rng.choice(w.people[a.account_id]),
                  campaign=campaign_id, channels=("events",), event_name=w.campaigns[campaign_id]["name"],
                  event_type="Conference")

    # LinkedIn ads: shown monthly to most accounts; they change nothing (the trap).
    for month in months_between(date(2024, 11, 1), w.end):
        for a in w.accounts:
            if not a.linkedin:
                continue
            w.add(a.account_id, "ad_shown", month, hour=0, campaign="CMP-07", channels=("linkedin",),
                  impressions=rng.randint(40, 1600), clicks=rng.randint(0, 6))
            start = engaged_from(a)
            if start and month > start and rng.random() < 0.04:
                w.add(a.account_id, "linkedin_engagement", plus(month, rng.randint(2, 25)), campaign="CMP-07",
                      channels=("linkedin",))

    # Website visits from identified companies, after their first engagement.
    for a in w.accounts:
        start = engaged_from(a)
        if not start:
            continue
        stop = closed_on[a.account_id] or w.end
        for month in months_between(start, stop):
            for _ in range(sum(1 for _ in range(3) if rng.random() < 0.2)):
                day = plus(month, rng.randint(0, 27))
                if start < day <= min(stop, w.end):
                    campaign = "CMP-12" if (date(2025, 1, 1) <= day <= date(2025, 12, 31) and rng.random() < 0.3) else "CMP-10"
                    w.add(a.account_id, "website_visit", day, campaign=campaign, channels=("web",),
                          page=rng.choice(["/insights/ai-controls", "/services/ai-assurance", "/events", "/about"]))

    # Declined referrals: the partner reported them, the client never agreed, nothing followed.
    for a in w.accounts:
        if a.referral == "declined":
            day = plus(w.referral_start, rng.randint(0, 500))
            w.add(a.account_id, "tech_partner_referral", day, person=w.person(a, "Influencer"), campaign="CMP-06",
                  channels=("referrals",), partner=rng.choice(TECH_PARTNERS), stated_need="", consent="N")

    # News and job postings: context, never a stage move.
    pool = rng.sample(w.accounts, 42)
    for a in pool:
        for _ in range(rng.choice([1, 1, 2])):
            day = plus(w.launch, rng.randint(0, (w.end - w.launch).days))
            if a.position == "Target" and rng.random() < 0.5:
                day = plus(w.end, -rng.randint(3, 28))   # a new trigger for an untouched account
            kind = rng.choice(["news_trigger", "job_posting"])
            headline = (f"{a.name} expands use of AI in underwriting and operations" if kind == "news_trigger"
                        else f"{a.name} is hiring a Head of AI Governance")
            w.add(a.account_id, kind, day, channels=("external",), headline=headline)

    # A few "not now" replies and contacts who left.
    candidates = [a for a in w.accounts if a.position in {"Aware", "Interested"} and a.plan.get("A")]
    for a in rng.sample(candidates, 6):
        day = min(plus(a.plan["A"], rng.randint(10, 60)), w.end)
        person = rng.choice(w.people[a.account_id])
        w.add(a.account_id, "negative_reply", day, person=person, campaign="CMP-08", channels=("email",),
              reply_text="Not now, thanks. We have no budget this year.")
    for a in rng.sample([a for a in w.accounts if a.position in {"Proposal / RFP", "Stalled", "Lost"}], 2):
        person = w.person(a, "Influencer")
        day = min(plus(a.plan.get("C") or a.plan.get("Q") or a.plan["A"], 30), w.end)
        w.add(a.account_id, "contact_left", day, person=person, channels=("notes",),
              sentence=f"{person.first} has left the company.")


# ----------------------------------------------------------------------------
# The truth: each account's real stage at every review point
# ----------------------------------------------------------------------------

def true_stage(w: World, a: Account, as_of: date, meaningful: dict) -> str:
    p = a.plan
    if p.get("S") and p["S"] <= as_of:
        return "Signed Agreement"
    if p.get("L") and p["L"] <= as_of:
        return "Lost"
    reached = [step for step in ["A", "I", "Q", "C", "P", "V"] if p.get(step) and p[step] <= as_of]
    if not reached:
        return "Target"
    furthest = max(reached, key=STEPS.index)
    stage = STEP_STAGE[furthest]
    if STEPS.index(furthest) >= STEPS.index("Q"):
        days = [d for d in meaningful.get(a.account_id, []) if d <= as_of]
        last = max(days) if days else p[furthest]
        if (as_of - last).days >= 45:
            return "Stalled"
    return stage


# ----------------------------------------------------------------------------
# Writing the export files, clean and messy
# ----------------------------------------------------------------------------

def us_date(day: date) -> str:
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


def day_first(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


class Renderer:
    """Writes every source file. With messy=True it adds the realistic mess."""

    def __init__(self, w: World, messy: bool):
        self.w, self.messy = w, messy
        # Two random streams: "base" for details that belong to the scenario (identical in
        # both copies), "rng" for the mess itself (used only by the messy copy).
        self.base = random.Random(w.sc["seed"] + 3)
        self.rng = random.Random(w.sc["seed"] + 7)
        self.mess = w.sc["mess"]
        self.counter = Counter()      # how much of each kind of mess was added
        self.people = {p.person_id: p for ps in w.people.values() for p in ps}
        self.accounts = {a.account_id: a for a in w.accounts}
        self.personal = {}
        self.probable_only = {a.account_id for a in w.accounts if a.tag == "probable_only"}
        if messy:
            persons = [p for p in self.people.values()]
            for p in self.rng.sample(persons, self.mess["personal_email_contacts"]):
                self.personal[p.person_id] = f"{p.first}.{p.last}{self.rng.randint(1, 99)}@{self.rng.choice(['gmail.com', 'outlook.com'])}".lower()

    def note(self, kind: str, n: int = 1) -> None:
        self.counter[kind] += n

    def email_for(self, person: Person | None) -> str:
        if person is None:
            return ""
        if self.messy and person.person_id in self.personal:
            return self.personal[person.person_id]
        return person.email

    @staticmethod
    def truth(account_id=None, person_id=None, event_ids=(), kind=""):
        return {"account_id": account_id or "", "person_id": person_id or "",
                "event_ids": ";".join(event_ids), "kind": kind}

    def events_on(self, channel: str) -> list[Event]:
        return sorted((e for e in self.w.events if channel in e.channels), key=lambda e: (e.when, e.event_id))

    # -- master lists -----------------------------------------------------------
    def target_accounts(self) -> list[dict]:
        rng, rows = self.rng, []
        missing = set()
        variants = {"Current client, other services": ["Audit client", "Y - tax", "current client", "Advisory client", ""],
                    "Former client": ["Former", "lapsed client", ""], "None": ["No", "Prospect", ""]}
        if self.messy:
            missing = {a.account_id for a in rng.sample(self.w.accounts[6:], self.mess["missing_domains_on_target_list"])}
            self.note("Missing website on the target list", len(missing))
        for a in self.w.accounts:
            relationship = a.relationship
            if self.messy and rng.random() < self.mess["inconsistent_relationship_share"]:
                relationship = rng.choice(variants[a.relationship])
                self.note("Existing relationship written inconsistently")
            rows.append({"account_id": a.account_id, "company_name": a.name,
                         "website": "" if a.account_id in missing else f"www.{a.domain}",
                         "industry": a.industry, "employees": a.size, "city": a.city,
                         "state_or_region": a.region, "country": a.country, "owner": a.owner,
                         "tier": a.tier, "existing_relationship": relationship,
                         "_truth": self.truth(a.account_id, kind="target account")})
        return rows

    def campaigns(self) -> list[dict]:
        rows = []
        missing = set()
        if self.messy:
            missing = set(self.rng.sample(sorted(self.w.campaigns), self.mess["missing_planned_budgets"]))
            self.note("Missing planned budget", len(missing))
        for c in self.w.sc["campaigns"]:
            rows.append({"campaign_id": c["id"], "campaign_name": c["name"], "channel": c["channel"],
                         "start_date": c["start"], "end_date": c["end"],
                         "planned_budget_usd": "" if c["id"] in missing else c["planned"],
                         "description": f"{c['channel']} program for the AI Assurance and Controls practice",
                         "_truth": self.truth(kind="campaign")})
        return rows

    def marketing_spend(self) -> list[dict]:
        """Monthly invoices that add up exactly to each program's actual spend."""
        rng, w = random.Random(w_seed(self.w) + 11), self.w   # same invoices in both copies
        category = {"Webinar": "Tools", "Content": "Content", "Roundtable or dinner": "Events",
                    "Conference": "Events", "Technology partners": "Partner programs", "LinkedIn": "Media",
                    "Email": "Tools", "Partner network": "Events", "Website": "Agency", "PR": "Agency",
                    "Paid media": "Media"}
        vendors = {"Media": "MediaWorks", "Events": "Venue Partners", "Content": "Northbeam Studio",
                   "Tools": "SaaS subscription", "Agency": "Halcyon Agency", "Partner programs": "Partner co-marketing"}
        invoices = []
        for c in w.sc["campaigns"]:
            start, end = date.fromisoformat(c["start"]), min(date.fromisoformat(c["end"]), w.end)
            months = list(months_between(start, end))
            if c["channel"] == "Conference":
                summit = date.fromisoformat(c["start"])
                deposit = date(summit.year, max(1, summit.month - 4), 1)
                parts = [(deposit, 0.3), (date(summit.year, summit.month, 1), 0.7)]
            elif c["channel"] == "Roundtable or dinner":
                dinner_months = sorted({date(d.year, d.month, 1) for d in w.dinners})
                parts = [(m, 1 / len(dinner_months)) for m in dinner_months]
            else:
                weights = [rng.uniform(0.6, 1.4) for _ in months]
                total = sum(weights)
                parts = [(m, x / total) for m, x in zip(months, weights)]
            amounts = [int(round(c["spend"] * share / 50.0)) * 50 for _, share in parts]
            amounts[-1] += c["spend"] - sum(amounts)
            for (month, _), amount in zip(parts, amounts):
                invoices.append({"month": month, "campaign_id": c["id"], "category": category[c["channel"]],
                                 "amount": amount})
        invoices.sort(key=lambda r: (r["month"], r["campaign_id"]))
        rows = []
        unassigned = set()
        if self.messy:
            unassigned = set(self.rng.sample(range(len(invoices)), self.mess["unassigned_spend_invoices"]))
            self.note("Invoice not assigned to a campaign", len(unassigned))
        for index, inv in enumerate(invoices, start=1):
            amount = inv["amount"]
            row = {"invoice_id": f"INV-{index:04d}", "month": inv["month"].strftime("%Y-%m"),
                   "campaign_id": "" if index - 1 in unassigned else inv["campaign_id"],
                   "category": inv["category"],
                   "amount_usd": f"${amount:,.2f}" if self.messy else amount,
                   "vendor": vendors[inv["category"]],
                   "memo": f"{w.campaigns[inv['campaign_id']]['name']} - {inv['month'].strftime('%b %Y')}",
                   "_truth": {**self.truth(kind="spend"), "campaign_id": inv["campaign_id"], "amount": amount}}
            rows.append(row)
        if self.messy:
            for _ in range(self.mess["duplicate_invoices"]):
                copy = dict(rows[self.rng.randrange(len(rows))])
                copy["_truth"] = {**copy["_truth"], "kind": "duplicate invoice"}
                rows.append(copy)
                self.note("The same invoice exported twice")
            rows.sort(key=lambda r: (r["month"], r["invoice_id"]))
        return rows

    # -- CRM ---------------------------------------------------------------------
    def crm_accounts(self) -> list[dict]:
        rng, rows = self.rng, []
        w = self.w
        duplicates = set()
        if self.messy:
            duplicates = {a.account_id for a in rng.sample([a for a in w.accounts if a.plan], self.mess["crm_duplicate_accounts"])}
            duplicates.add("ACC-001")
            self.note("Duplicate CRM company record", len(duplicates))
        for index, a in enumerate(w.accounts, start=1):
            a.crm_id = f"001A{index:05d}"
            target_id, website = a.account_id, f"www.{a.domain}"
            if self.messy:
                if a.account_id in self.probable_only:
                    target_id, website = "", ""
                    self.note("CRM record with no target ID or website")
                elif rng.random() < self.mess["crm_missing_target_id_share"]:
                    target_id = ""
                    self.note("CRM record missing its target account ID")
                    website = rng.choice([f"http://{a.domain}", f"https://www.{a.domain}/", f"{a.domain}", website, ""])
            rows.append({"crm_account_id": a.crm_id, "account_name": a.name, "website": website,
                         "billing_country": a.country, "target_account_id": target_id, "owner": a.owner,
                         "created_date": self.fmt_crm_date(plus(w.launch, -45)),
                         "_truth": self.truth(a.account_id, kind="crm account")})
        if self.messy:
            for a in w.accounts:
                if a.account_id in duplicates:
                    a.crm_dup_id = f"001B{int(a.crm_id[4:]):05d}"
                    a.crm_dup_name = {"ABC Corp": "ABC Corporation"}.get(a.name, a.name + (" Inc" if rng.random() < 0.5 else " Corporation"))
                    rows.append({"crm_account_id": a.crm_dup_id, "account_name": a.crm_dup_name, "website": "",
                                 "billing_country": "", "target_account_id": "", "owner": a.owner,
                                 "created_date": self.fmt_crm_date(plus(w.launch, rng.randint(60, 400))),
                                 "_truth": self.truth(a.account_id, kind="duplicate crm account")})
        return rows

    def fmt_crm_date(self, day: date) -> str:
        return us_date(day) if self.messy else day.isoformat()

    def crm_contacts(self) -> list[dict]:
        rng, rows, w = self.rng, [], self.w
        index = 0
        people = sorted((p for ps in w.people.values() for p in ps if p.in_crm_from), key=lambda p: (p.in_crm_from, p.person_id))
        outdated = set()
        if self.messy:
            eligible = [p for p in people if p.title in OLD_TITLES]
            outdated = {p.person_id for p in rng.sample(eligible, min(self.mess["outdated_job_titles"], len(eligible)))}
            self.note("Out-of-date job title in the CRM", len(outdated))
        for p in people:
            index += 1
            p.crm_contact_id = f"003C{index:05d}"
            a = self.accounts[p.account_id]
            email, contact_id, title = p.email, p.crm_contact_id, p.title
            if self.messy:
                if p.account_id in self.probable_only or rng.random() < self.mess["contacts_missing_email_share"]:
                    email = ""
                    self.note("CRM contact with no email")
                if rng.random() < self.mess["contacts_missing_id_share"]:
                    contact_id = ""
                    self.note("CRM contact with no contact ID")
                if p.person_id in outdated:
                    title = OLD_TITLES[p.title]
            rows.append({"crm_contact_id": contact_id, "crm_account_id": a.crm_id, "first_name": p.first,
                         "last_name": p.last, "email": email, "title": title,
                         "last_modified": self.fmt_crm_date(p.in_crm_from),
                         "_truth": self.truth(p.account_id, p.person_id, kind="crm contact")})
        return rows

    def crm_opportunities(self) -> list[dict]:
        rng, rows, w = self.rng, [], self.w
        stage_name = {"Q": "Qualified Lead", "C": "Active Conversation", "P": "Proposal / RFP",
                      "V": "Verbal Commitment", "S": "Signed Agreement"}
        source_label = {"Technology partner referral": "Technology partner referral",
                        "Executive roundtable": "Executive roundtable", "Partner introduction": "Partner introduction",
                        "Conference and existing relationship": "Existing client", "RFP-led": "RFP",
                        "Marketing-led": "Marketing"}
        index = 0
        for a in w.accounts:
            p = a.plan
            created = p.get("C") or p.get("Q") or p.get("P")
            if not created or a.position not in {"Signed Agreement", "Lost", "Verbal Commitment", "Proposal / RFP",
                                                 "Active Conversation", "Qualified Lead", "Stalled"}:
                continue
            index += 1
            reached = [s for s in ["Q", "C", "P", "V", "S"] if p.get(s) and p[s] <= w.end]
            stage = stage_name[reached[-1]] if reached else "Qualified Lead"
            status, closed, reason = "Open", "", ""
            if a.position == "Signed Agreement":
                status, closed = "Won", p["S"]
            elif a.position == "Lost":
                status, closed, reason, stage = "Lost", p["L"], a.loss_reason, "Closed Lost"
            amount, lead_source = a.value_usd, source_label.get(a.journey, "Marketing")
            expected = plus(created, self.base.randint(240, 360))
            service_line = self.base.choice(["AI governance assessment", "AI controls assurance", "Model risk review"])
            if self.messy:
                if a.tag == "stale_crm_verbal":
                    stage = "Verbal Commitment"
                    self.note("CRM stage later than the evidence supports")
                if a.tag == "stale_crm_proposal":
                    stage = "Proposal / RFP"
                    self.note("CRM stage later than the evidence supports")
                if rng.random() < self.mess["lead_source_after_the_fact_share"]:
                    lead_source = rng.choice(["Website", "Webinar", "Other", ""])
                    self.note("Lead source filled in after the fact")
                if reason and rng.random() < self.mess["missing_loss_reason_share"]:
                    reason = ""
                    self.note("Missing loss reason")
                if rng.random() < self.mess["missing_amount_share"]:
                    amount = None
                    self.note("Missing deal amount")
            rows.append({"opportunity_id": f"006D{index:05d}", "crm_account_id": a.crm_id,
                         "opportunity_name": f"{a.name} - AI governance assessment",
                         "service_line": service_line,
                         "amount_usd": "" if amount is None else amount, "stage": stage,
                         "lead_source": lead_source, "status": status, "loss_reason": reason,
                         "created_date": self.fmt_crm_date(created),
                         "expected_close_date": self.fmt_crm_date(expected),
                         "closed_date": self.fmt_crm_date(closed) if closed else "",
                         "_truth": {**self.truth(a.account_id, kind="opportunity"), "true_value": a.value_usd or ""}})
        return rows

    def crm_activities(self) -> list[dict]:
        rng, rows, w = self.rng, [], self.w
        events = self.events_on("crm")
        if self.messy:   # the CRM also logs many calendar meetings: the classic duplicate
            for e in self.events_on("calendar"):
                if e.extra.get("status") == "held" and rng.random() < self.mess["crm_logs_calendar_meetings_share"]:
                    events.append(e)
                    self.note("Meeting logged in both the calendar and the CRM")
            events.sort(key=lambda e: (e.when, e.event_id))
        for index, e in enumerate(events, start=1):
            a = self.accounts[e.account_id]
            person = self.people.get(e.person_id)
            crm_account = a.crm_id
            if self.messy and a.crm_dup_id and rng.random() < 0.3:
                crm_account = a.crm_dup_id
                self.note("Activity logged under a duplicate CRM company")
            contact_id = person.crm_contact_id if person and person.crm_contact_id else ""
            activity = e.extra.get("activity") or e.extra.get("category", "Call")
            subject = e.extra.get("subject") or f"{activity} - {a.name}"
            if self.messy and rng.random() < 0.5:
                subject = shorthand(subject)
            rows.append({"activity_id": f"00T{index:06d}", "crm_account_id": crm_account, "crm_contact_id": contact_id,
                         "activity_date": self.fmt_crm_date(e.when.date()), "activity_type": activity,
                         "subject": subject, "owner": a.owner,
                         "_truth": self.truth(a.account_id, e.person_id, [e.event_id], e.kind)})
        return rows

    def meetings(self) -> list[dict]:
        rng, rows = self.rng, []
        for index, e in enumerate(self.events_on("calendar"), start=1):
            a = self.accounts[e.account_id]
            attendees = [self.people[pid] for pid in e.extra.get("attendees", [])]
            emails = [self.email_for(p) for p in attendees]
            # ABC Corp is the design document's worked example, so its calendar is kept intact.
            if self.messy and a.account_id != "ACC-001" and rng.random() < self.mess["calendar_missing_attendees_share"]:
                emails = []
                self.note("Calendar entry with no outside attendees")
            internal = OWNER_EMAIL[a.owner]
            offset = "-04:00" if 3 <= e.when.month <= 10 else "-05:00"
            local = e.when.replace(tzinfo=None) - timedelta(hours=4 if offset == "-04:00" else 5)
            rows.append({"meeting_id": f"MTG-{index:05d}", "start_time": local.strftime("%Y-%m-%dT%H:%M:%S") + offset,
                         "subject": e.extra.get("subject", ""), "organizer": internal,
                         "attendees": ";".join([internal] + emails),
                         "status": e.extra.get("status", "held"), "category": e.extra.get("category", ""),
                         "_truth": self.truth(a.account_id, e.person_id, [e.event_id], e.kind)})
        return rows

    def meeting_notes(self) -> list[dict]:
        """Group the sentences partners wrote into notes, one per account, day and author."""
        rng, w = self.rng, self.w
        groups: dict[tuple, list[Event]] = defaultdict(list)
        for e in self.events_on("notes"):
            groups[(e.account_id, e.when.date())].append(e)
        rows = []
        nicknames = {"ABC Corp": "ABC", "Harborline Insurance": "Harborline", "Meridian Health": "Meridian"}
        for index, ((account_id, day), events) in enumerate(sorted(groups.items()), start=1):
            a = self.accounts[account_id]
            first_person = self.people.get(events[0].person_id)
            text = " ".join(e.sentence for e in events if e.sentence)
            if self.base.random() < 0.5:
                text += " " + self.base.choice(["Good discussion overall.", "Next step: send a recap.",
                                                "Will follow up next week.", "Owner to update the CRM."])
            note_type = self.base.choice(["Partner note", "Call note", "Email thread"])
            note_date, crm_account, company, contact = day.isoformat(), a.crm_id, a.name, ""
            if first_person and self.base.random() < 0.6:
                contact = first_person.email
            if self.messy:
                if account_id in self.probable_only:
                    crm_account, contact = "", ""
                    self.note("Note linked only by company name")
                elif rng.random() < self.mess["note_missing_account_share"]:
                    crm_account = ""
                    self.note("Note not attached to a CRM record")
                    if rng.random() < self.mess["note_nickname_share"]:
                        company = nicknames.get(a.name, a.name.split()[0])
                        self.note("Company nickname in a note")
                roll = rng.random()
                if roll < self.mess["note_missing_date_share"]:
                    note_date = ""
                    self.note("Note with no date")
                elif roll < self.mess["note_missing_date_share"] + self.mess["note_unreadable_date_share"]:
                    note_date = rng.choice(["next Tues", "Q3", "after the board mtg", f"{day.month}/{day.day}"])
                    self.note("Note with an unreadable date")
                elif roll < 0.3:
                    note_date = day.strftime("%b %d, %Y").replace(" 0", " ")
            rows.append({"note_id": f"NOTE-{index:04d}", "note_date": note_date,
                         "author": a.owner, "note_type": note_type,
                         "crm_account_id": crm_account, "company": company, "contact_email": contact, "text": text,
                         "_truth": self.truth(account_id, events[0].person_id, [e.event_id for e in events], "note")})
        return rows

    # -- marketing systems -------------------------------------------------------
    def email_events(self) -> list[dict]:
        rng, rows = self.rng, []
        events = self.events_on("email")
        extra_rows = []
        for e in events:
            person = self.people.get(e.person_id)
            kind = {"outreach_sent": "sent", "email_opened": "opened", "email_replied": "replied",
                    "need_identified": "replied", "negative_reply": "replied", "unsubscribed": "unsubscribed"}[e.kind]
            reply = e.extra.get("reply_text", "") if kind == "replied" else ""
            rows.append((e.when, kind, person, e.campaign_id, reply, self.truth(e.account_id, e.person_id, [e.event_id], e.kind)))
            if self.messy and kind == "sent" and rng.random() < self.mess["machine_open_share"]:
                extra_rows.append((e.when + timedelta(seconds=rng.randint(2, 40)), "opened", person, e.campaign_id, "",
                                   self.truth(e.account_id, e.person_id, [e.event_id], "machine open")))
                self.note("Email open made by a mail app, not a person")
        rows += extra_rows
        rows.sort(key=lambda r: (r[0], r[1]))
        out = []
        unreadable = set()
        if self.messy:
            unreadable = set(rng.sample(range(len(rows)), self.mess["unreadable_timestamps"] // 2))
            self.note("Unreadable timestamp", len(unreadable))
        for index, (when, kind, person, campaign, reply, truth) in enumerate(rows, start=1):
            stamp = str(int(when.timestamp())) if self.messy else iso_z(when)
            if index - 1 in unreadable:
                stamp = rng.choice(["#VALUE!", "0", "N/A"])
            out.append({"event_id": f"EM-{index:06d}", "event_time": stamp, "recipient_email": self.email_for(person),
                        "campaign_id": campaign or "", "event_type": kind, "reply_text": reply, "_truth": truth})
        return self.repeat_rows(out, self.mess["repeated_rows"] // 2)

    def web_visits(self) -> list[dict]:
        rng, w = self.rng, self.w
        rows = []
        for e in self.events_on("web"):
            a = self.accounts[e.account_id]
            person = self.people.get(e.person_id)
            company, domain = a.name, a.domain
            if self.messy and e.kind == "website_visit" and rng.random() < self.mess["web_wrong_company_share"]:
                company, domain = rng.choice(NETWORK_GUESSES), ""
                self.note("Website visitor's company guessed wrong")
            elif self.messy and e.kind == "website_visit" and rng.random() < 0.25:
                domain = ""
            action = "download" if e.kind == "content_downloaded" else "page_view"
            rows.append({"when": e.when, "visitor_company": company, "visitor_domain": domain,
                         "form_email": self.email_for(person) if action == "download" else "",
                         "action": action, "asset": e.extra.get("asset", ""), "page": e.extra.get("page", "/"),
                         "utm_campaign": e.campaign_id if e.campaign_id in {"CMP-12", "CMP-02"} else "",
                         "_truth": self.truth(a.account_id, e.person_id, [e.event_id], e.kind)})
        # Anonymous traffic: the same in both copies, because nobody can identify it.
        noise = random.Random(w_seed(w) + 21)
        for _ in range(900):
            day = plus(w.launch, noise.randint(0, (w.end - w.launch).days))
            rows.append({"when": at(day, noise.randint(0, 23), noise.randint(0, 59)), "visitor_company": "",
                         "visitor_domain": "", "form_email": "", "action": "page_view", "asset": "",
                         "page": noise.choice(["/", "/insights/ai-controls", "/careers", "/services/ai-assurance"]),
                         "utm_campaign": "CMP-12" if noise.random() < 0.15 and day.year == 2025 else "",
                         "_truth": self.truth(kind="anonymous visit")})
        rows.sort(key=lambda r: (r["when"], r["visitor_company"]))
        out = []
        for index, r in enumerate(rows, start=1):
            when = r.pop("when")
            out.append({"visit_id": f"WV-{index:06d}", "visit_time": iso_z(when), **r})
        if self.messy:
            for index in rng.sample(range(len(out)), self.mess["unreadable_timestamps"] // 2):
                out[index]["visit_time"] = rng.choice(["", "1970-01-01T00:00:00Z"])
                self.note("Unreadable timestamp")
        return self.repeat_rows(out, self.mess["repeated_rows"] // 2)

    def event_attendance(self) -> list[dict]:
        rng, rows = self.rng, []
        typed = {"ABC Corp": ["ABC Corporation", "abc corp", "ABC Corp Ltd"],
                 "Harborline Insurance": ["Harbourline", "Harborline Ins."],
                 "Summit Financial Group": ["Summit Financial"], "Meridian Health": ["Meridian Health", "Meridian"]}
        for index, e in enumerate(self.events_on("events"), start=1):
            a = self.accounts[e.account_id]
            person = self.people.get(e.person_id)
            status = {"webinar_registered": "registered", "webinar_attended": "attended",
                      "webinar_no_show": "no-show", "event_attended": "attended",
                      "conference_conversation": "attended"}[e.kind]
            day = e.when.date()
            company, email = a.name, self.email_for(person)
            exported = day
            if self.messy:
                exported = plus(day, rng.randint(*self.mess["event_late_days"]))
                if rng.random() < self.mess["event_typed_company_share"]:
                    company = rng.choice(typed.get(a.name, [a.name.lower(), a.name.replace(" ", "  "), a.name.split()[0] + " " + a.name.split()[-1][:3] + "."]))
                    self.note("Company name typed by the attendee")
                if a.account_id in self.probable_only or rng.random() < self.mess["event_missing_email_share"]:
                    email = ""
                    self.note("Attendee with no email")
            rows.append({"registration_id": f"REG-{index:05d}", "event_name": e.extra.get("event_name", ""),
                         "event_type": e.extra.get("event_type", ""),
                         "event_date": day_first(day) if self.messy else day.isoformat(),
                         "campaign_id": e.campaign_id or "", "registrant_name": person.name if person else "",
                         "email": email, "company": company, "job_title": person.title if person else "",
                         "status": status, "exported_at": exported.isoformat(),
                         "_truth": self.truth(a.account_id, e.person_id, [e.event_id], e.kind)})
        if self.messy:
            self.note("Event data arriving 5 to 9 days late", len(rows))
        return rows

    def partner_referrals(self) -> list[dict]:
        rows = []
        for index, e in enumerate(self.events_on("referrals"), start=1):
            a = self.accounts[e.account_id]
            person = self.people.get(e.person_id)
            day = e.when.date()
            rows.append({"referral_id": f"REF-{index:03d}", "partner_name": e.extra.get("partner", ""),
                         "referral_date": day.strftime("%B %d, %Y").replace(" 0", " ") if self.messy else day.isoformat(),
                         "company_name": a.name, "company_website": "" if self.messy else a.domain,
                         "contact_name": person.name if person else "", "contact_title": person.title if person else "",
                         "contact_email": "" if self.messy else (person.email if person else ""),
                         "stated_need": e.extra.get("stated_need", ""), "client_agreed": e.extra.get("consent", "Y"),
                         "_truth": self.truth(a.account_id, e.person_id, [e.event_id], e.kind)})
        if self.messy:
            self.note("Referral reported by company name only", len(rows))
        return rows

    def external_signals(self) -> list[dict]:
        rows = []
        for index, e in enumerate(self.events_on("external"), start=1):
            a = self.accounts[e.account_id]
            rows.append({"id": f"EXT-{index:04d}", "date": e.when.date().isoformat(), "company_name": a.name,
                         "company_website": "" if self.messy else a.domain,
                         "kind": "news" if e.kind == "news_trigger" else "job posting",
                         "headline": e.extra.get("headline", ""),
                         "_truth": self.truth(a.account_id, None, [e.event_id], e.kind)})
        if self.messy:   # noise: news about companies that are not on the target list
            for index in range(8):
                rows.append({"id": f"EXT-9{index:03d}", "date": plus(self.w.launch, 30 * index + 11).isoformat(),
                             "company_name": self.rng.choice(["Meridian Logistics", "Summit Retail", "Granite Foods",
                                                              "ABC Supply"]),
                             "company_website": "", "kind": "news",
                             "headline": "Company announces new AI program",
                             "_truth": self.truth(kind="noise")})
                self.note("News about a company that is not a target")
        return rows

    def linkedin_ads(self) -> list[dict]:
        rows = []
        grouped: dict[tuple, list[Event]] = defaultdict(list)
        for e in self.events_on("linkedin"):
            grouped[(e.when.strftime("%Y-%m"), e.account_id)].append(e)
        official = {"ABC Corp": "ABC Corp Ltd", "Harborline Insurance": "Harborline Insurance Company"}
        for (month, account_id), events in sorted(grouped.items()):
            a = self.accounts[account_id]
            shown = [e for e in events if e.kind == "ad_shown"]
            engaged = [e for e in events if e.kind == "linkedin_engagement"]
            impressions = sum(e.extra.get("impressions", 0) for e in shown)
            clicks = sum(e.extra.get("clicks", 0) for e in shown)
            rows.append({"report_month": month, "campaign_id": "CMP-07",
                         "company_name": official.get(a.name, a.name) if self.messy else a.name,
                         "company_website": "" if self.messy else a.domain,
                         "impressions": impressions, "clicks": clicks, "engagements": len(engaged),
                         "_truth": self.truth(a.account_id, None, [e.event_id for e in events], "linkedin month")})
        return rows

    # -- utilities ---------------------------------------------------------------
    def repeat_rows(self, rows: list[dict], n: int) -> list[dict]:
        """The same row exported twice, as real exports sometimes do."""
        if not self.messy or not rows:
            return rows
        for index in sorted(self.rng.sample(range(len(rows)), n), reverse=True):
            copy = dict(rows[index])
            copy["_truth"] = {**copy["_truth"], "kind": "repeated row"}
            rows.insert(index + 1, copy)
            self.note("The same row exported twice")
        return rows

    def render_all(self) -> dict[str, list[dict]]:
        return {
            "target_accounts.csv": self.target_accounts(),
            "campaigns.csv": self.campaigns(),
            "marketing_spend.csv": self.marketing_spend(),
            "crm_accounts.csv": self.crm_accounts(),
            "crm_contacts.csv": self.crm_contacts(),
            "crm_opportunities.csv": self.crm_opportunities(),
            "crm_activities.csv": self.crm_activities(),
            "meetings.csv": self.meetings(),
            "meeting_notes.jsonl": self.meeting_notes(),
            "email_events.csv": self.email_events(),
            "web_visits.csv": self.web_visits(),
            "event_attendance.csv": self.event_attendance(),
            "partner_referrals.csv": self.partner_referrals(),
            "external_signals.json": self.external_signals(),
            "linkedin_ads.csv": self.linkedin_ads(),
        }


def w_seed(w: World) -> int:
    return int(w.sc["seed"])


def shorthand(subject: str) -> str:
    """How people really type CRM subjects."""
    for long, short in [("meeting", "mtg"), ("Meeting", "Mtg"), ("discovery", "disc."), ("proposal", "prop."),
                        ("Proposal", "Prop."), ("with", "w/"), ("Calder Hollis", "CH"), ("call", "call")]:
        subject = subject.replace(long, short)
    return subject


# ----------------------------------------------------------------------------
# Writing files and the answer key
# ----------------------------------------------------------------------------

def write_files(folder: Path, files: dict[str, list[dict]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in files.items():
        clean_rows = [{k: v for k, v in r.items() if k != "_truth"} for r in rows]
        path = folder / name
        if name.endswith(".csv"):
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(clean_rows[0].keys()))
                writer.writeheader()
                writer.writerows(clean_rows)
        elif name.endswith(".jsonl"):
            with path.open("w", encoding="utf-8") as handle:
                for row in clean_rows:
                    handle.write(json.dumps(row) + "\n")
        else:
            path.write_text(json.dumps(clean_rows, indent=2), encoding="utf-8")


def write_answer_key(folder: Path, w: World, clean: dict, messy: dict, mess_counts: Counter) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    review_points = config.review_points()
    meaningful = defaultdict(list)
    for e in w.events:
        if e.kind in MEANINGFUL_KINDS:
            meaningful[e.account_id].append(e.when.date())

    with (folder / "accounts_truth.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["account_id", "company_name", "domain", "tier", "existing_relationship", "final_position",
                  "lost_at", "loss_reason", "journey", "tag", "roundtable_attendee", "technology_referral",
                  "partner_introduction", "rfp_led", "linkedin_ads_shown", "deal_value_usd"]
        fields += [f"{step}_date" for step in ["A", "I", "Q", "C", "P", "V", "S", "L"]]
        fields += [f"stage_{d}" for d, _ in review_points]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for a in w.accounts:
            row = {"account_id": a.account_id, "company_name": a.name, "domain": a.domain, "tier": a.tier,
                   "existing_relationship": a.relationship, "final_position": a.position,
                   "lost_at": a.lost_at or "", "loss_reason": a.loss_reason or "", "journey": a.journey,
                   "tag": a.tag or "", "roundtable_attendee": int(a.roundtable), "technology_referral": a.referral,
                   "partner_introduction": int(a.intro), "rfp_led": int(a.rfp_led),
                   "linkedin_ads_shown": int(a.linkedin), "deal_value_usd": a.value_usd or ""}
            for step in ["A", "I", "Q", "C", "P", "V", "S", "L"]:
                row[f"{step}_date"] = a.plan[step].isoformat() if a.plan.get(step) else ""
            for d, _ in review_points:
                row[f"stage_{d}"] = true_stage(w, a, date.fromisoformat(d), meaningful)
            writer.writerow(row)

    with (folder / "people_truth.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["person_id", "account_id", "name", "email", "title", "role",
                                                    "crm_contact_id"])
        writer.writeheader()
        for people in w.people.values():
            for p in people:
                writer.writerow({"person_id": p.person_id, "account_id": p.account_id, "name": p.name,
                                 "email": p.email, "title": p.title, "role": p.role,
                                 "crm_contact_id": p.crm_contact_id or ""})

    for variant, files in [("messy", messy), ("clean", clean)]:
        with (folder / f"record_truth_{variant}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["file", "source_row", "account_id", "person_id",
                                                        "event_ids", "kind"])
            writer.writeheader()
            for name, rows in files.items():
                for index, row in enumerate(rows, start=1):
                    t = row["_truth"]
                    writer.writerow({"file": name, "source_row": index, "account_id": t["account_id"],
                                     "person_id": t["person_id"], "event_ids": t["event_ids"], "kind": t["kind"]})

    with (folder / "note_facts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_id", "account_id", "date", "fact", "sentence",
                                                    "known_phrasing"])
        writer.writeheader()
        for e in sorted(w.events, key=lambda e: e.when):
            if "notes" in e.channels and e.sentence:
                writer.writerow({"event_id": e.event_id, "account_id": e.account_id, "date": e.when.date().isoformat(),
                                 "fact": e.kind, "sentence": e.sentence, "known_phrasing": int(e.recognized)})

    true_spend = defaultdict(int)
    for row in clean["marketing_spend.csv"]:
        true_spend[row["_truth"]["campaign_id"]] += row["_truth"]["amount"]
    planted = {
        "note": "Patterns planted on purpose. The system never reads this file.",
        "real_effects": {
            "roundtables": {
                "campaign": "CMP-03", "signal_type": "event_attended",
                "accounts": [a.account_id for a in w.accounts if a.roundtable],
                "expected": "Association or better in the advanced-versus-not comparison"},
            "technology_partner_referrals": {
                "campaign": "CMP-06", "signal_type": "tech_partner_referral",
                "accounts": [a.account_id for a in w.accounts if a.referral == "accepted"],
                "declined": [a.account_id for a in w.accounts if a.referral == "declined"],
                "expected": "Association or better in the advanced-versus-not comparison"},
        },
        "traps": {
            "report_after_progress": {"campaign": "CMP-02", "signal_type": "content_downloaded",
                                      "expected": "Never a Consistent pattern: most downloads come after progress"},
            "current_clients_buy_anyway": {"campaign": "CMP-01", "signal_type": "webinar_attended",
                                           "expected": "Never a Consistent pattern: the difference disappears within current clients and new relationships"},
            "linkedin_ads_change_nothing": {"campaign": "CMP-07", "signal_type": "ad_shown",
                                            "expected": "Budget verdict Reduce or Stop"},
        },
        "true_spend_by_campaign": dict(sorted(true_spend.items())),
    }
    (folder / "planted_patterns.yaml").write_text(yaml.safe_dump(planted, sort_keys=False), encoding="utf-8")

    with (folder / "mess_checklist.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind_of_mess", "rows_affected"])
        for kind, count in sorted(mess_counts.items()):
            writer.writerow([kind, count])


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def build_world(scenario: dict | None = None) -> World:
    w = World(scenario or config.scenario())
    make_accounts(w)
    assign_planted_flags(w)
    assign_positions(w)
    plan_journeys(w)
    for a in w.accounts:
        journey_events(w, a)
    marketing_events(w)
    w.events = [e for e in w.events if e.when.date() <= w.end]
    return w


def generate(out_dir: Path | None = None, scenario: dict | None = None, quiet: bool = False) -> dict:
    """Build the scenario and write raw, clean and answer-key folders. Returns a summary."""
    data_dir = Path(out_dir) if out_dir else config.PROJECT_ROOT / "data"
    w = build_world(scenario)
    clean_files = Renderer(w, messy=False).render_all()
    messy_renderer = Renderer(w, messy=True)
    messy_files = messy_renderer.render_all()
    write_files(data_dir / "clean", clean_files)
    write_files(data_dir / "raw", messy_files)
    write_answer_key(data_dir / "answer_key", w, clean_files, messy_files, messy_renderer.counter)
    positions = Counter(a.position for a in w.accounts)
    summary = {
        "accounts": len(w.accounts),
        "people": sum(len(v) for v in w.people.values()),
        "events": len(w.events),
        "positions": dict(positions),
        "signed_by_goal_date": sum(1 for a in w.accounts if a.plan.get("S") and a.plan["S"] <= w.goal),
        "rows_messy": {name: len(rows) for name, rows in messy_files.items()},
        "rows_clean": {name: len(rows) for name, rows in clean_files.items()},
        "mess": dict(messy_renderer.counter),
        "spend_total": sum(r["_truth"]["amount"] for r in clean_files["marketing_spend.csv"]),
    }
    if not quiet:
        print_summary(summary, data_dir)
    return summary


def print_summary(summary: dict, data_dir: Path) -> None:
    print("Synthetic scenario written.")
    print(f"  {summary['accounts']} target accounts, {summary['people']} people, {summary['events']} true events")
    print(f"  Marketing spend: ${summary['spend_total']:,}")
    print(f"  Signed by the 18-month goal date: {summary['signed_by_goal_date']}")
    print("  Where the accounts end up:")
    for position, count in sorted(summary["positions"].items(), key=lambda kv: -kv[1]):
        print(f"    {position:22s} {count}")
    print(f"  Raw files in {data_dir / 'raw'} (rows, messy / clean):")
    for name, count in summary["rows_messy"].items():
        print(f"    {name:26s} {count:6d} / {summary['rows_clean'][name]}")
    print(f"  Kinds of mess added: {len(summary['mess'])} (full list in data/answer_key/mess_checklist.csv)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic Calder Hollis dataset.")
    parser.add_argument("--out", help="folder to write into (default: the project's data folder)")
    args = parser.parse_args(argv)
    generate(Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
