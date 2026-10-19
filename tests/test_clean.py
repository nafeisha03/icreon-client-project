"""Milestone 4: reading dates, emails, domains, names and amounts the way each source writes them."""
import pytest

from calder_abm.clean import (clean_domain, clean_email, clean_money, is_personal_domain, map_relationship,
                              normalize_company, parse_when)
from calder_abm.identity import infer_role, infer_seniority


@pytest.mark.parametrize("value, fmt, expected", [
    ("2026-03-04", "any", "2026-03-04T12:00:00Z"),                   # a date: noon UTC, precision 'day'
    ("2026-03-04T09:30:00-05:00", "any", "2026-03-04T14:30:00Z"),     # time zones converted to UTC
    ("04/03/2026", "us", "2026-04-03T12:00:00Z"),                     # the CRM writes the month first
    ("04/03/2026", "day_first", "2026-03-04T12:00:00Z"),              # the events platform writes the day first
    ("25/03/2026", "any", "2026-03-25T12:00:00Z"),                    # only one reading is possible
    ("1772618400", "any", "2026-03-04T10:00:00Z"),                    # Unix seconds from the email platform
    ("2025-11", "any", "2025-11-01T12:00:00Z"),                       # a month, for monthly reports
    ("March 4, 2026", "any", "2026-03-04T12:00:00Z"),
])
def test_dates_are_read_in_each_sources_format(value, fmt, expected):
    iso, _, problem = parse_when(value, fmt)
    assert problem is None
    assert iso == expected


@pytest.mark.parametrize("value, problem", [
    ("04/03/2026", "ambiguous"),     # unknown habit and both readings possible: never guessed
    ("#VALUE!", "unreadable"),
    ("next Tues", "unreadable"),
    ("", "missing"),
    (None, "missing"),
])
def test_unreadable_or_ambiguous_dates_are_left_empty(value, problem):
    iso, precision, found = parse_when(value, "any")
    assert iso is None and precision == "unknown" and found == problem


def test_time_precision_is_kept():
    assert parse_when("2026-03-04T09:30:00Z")[1] == "exact"
    assert parse_when("2026-03-04")[1] == "day"


def test_emails_and_domains_are_cleaned():
    assert clean_email("  Mei.Moreau@ABCCorp.com ") == "mei.moreau@abccorp.com"
    assert clean_email("not an email") is None
    assert clean_email("mei@localhost") is None
    assert clean_domain("https://www.ABCCorp.com/about?x=1") == "abccorp.com"
    assert clean_domain("www.meridianhealth.ca") == "meridianhealth.ca"
    assert clean_domain("n/a") is None
    assert is_personal_domain("gmail.com") and not is_personal_domain("abccorp.com")


def test_company_names_compare_in_cleaned_form():
    assert normalize_company("ABC Corp.") == normalize_company("abc corporation") == normalize_company("ABC Corp Ltd")
    assert normalize_company("Harbor & Main Inc") == "harbor and main"
    # Words that tell two companies apart are kept.
    assert normalize_company("Summit Financial Group") != normalize_company("Summit Financial Holdings")


def test_money_and_relationships():
    assert clean_money("$340,000") == 340000
    assert clean_money("") is None and clean_money("tbd") is None
    assert map_relationship("Y - tax") == "Current client, other services"
    assert map_relationship("Prospect") == "None"
    assert map_relationship("lapsed client") == "Former client"
    assert map_relationship("") == "Unknown"          # blank is unknown, never guessed


def test_buying_roles_and_seniority_from_titles():
    assert infer_role("General Counsel") == "Legal Reviewer"
    assert infer_role("Chief Risk Officer") in {"Decision Maker", "Executive Sponsor"}
    assert infer_role("Head of Internal Audit") == "Decision Maker"
    assert infer_role("") == "Unknown"
    assert infer_seniority("VP, Internal Audit") == "VP"
    assert infer_seniority("Chief Financial Officer") == "C-level"
