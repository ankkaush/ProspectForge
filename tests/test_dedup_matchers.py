import uuid
from datetime import datetime, timezone

from app.orm import AccountORM, ContactORM
from prospectforge.dedup.matchers import (
    accounts_match_reason,
    contacts_match_reason,
    normalize_company_name,
    normalize_domain,
    normalize_email,
)


def _account(domain: str, name: str) -> AccountORM:
    return AccountORM(id=uuid.uuid4(), domain=domain, name=name, created_at=datetime.now(timezone.utc))


def _contact(name: str, email=None) -> ContactORM:
    return ContactORM(
        id=uuid.uuid4(), account_id=uuid.uuid4(), name=name, email=email,
        created_at=datetime.now(timezone.utc),
    )


# --- normalization -----------------------------------------------------

def test_normalize_domain_strips_www_and_protocol():
    assert normalize_domain("https://www.Example.com/") == "example.com"
    assert normalize_domain("example.com") == "example.com"


def test_normalize_company_name_strips_legal_suffixes():
    assert normalize_company_name("Northstar Metrics Inc.") == "northstar metrics"
    assert normalize_company_name("Northstar Metrics") == "northstar metrics"
    assert normalize_company_name("Acme LLC") == "acme"


def test_normalize_email_is_case_insensitive():
    assert normalize_email("Jane.Doe@Example.COM") == "jane.doe@example.com"


# --- account matching ----------------------------------------------------

def test_www_prefix_variant_is_a_domain_match():
    a = _account("northstar-metrics.com", "Northstar Metrics")
    b = _account("www.northstar-metrics.com", "Northstar Metrics")
    reason = accounts_match_reason(a, b)
    assert reason is not None
    assert "normalized domain" in reason


def test_legal_suffix_variant_is_a_name_similarity_match():
    a = _account("northstar-metrics.com", "Northstar Metrics")
    b = _account("northstarmetrics-inc.com", "Northstar Metrics Inc.")
    reason = accounts_match_reason(a, b)
    assert reason is not None
    assert "name similarity" in reason


def test_genuinely_different_companies_do_not_match():
    a = _account("northstar-metrics.com", "Northstar Metrics")
    b = _account("verdantanalytics.com", "Verdant Analytics")
    assert accounts_match_reason(a, b) is None


def test_similar_but_distinct_company_names_do_not_match():
    """The over-aggressive-matching guard: these are two real, different
    companies that happen to share a word - the conservative threshold
    must not merge them."""

    a = _account("northstar-metrics.com", "Northstar Metrics")
    b = _account("northstar-analytics.com", "Northstar Analytics")
    assert accounts_match_reason(a, b) is None


# --- contact matching ------------------------------------------------------

def test_same_normalized_email_is_a_match():
    a = _contact("Jane Doe", email="jane.doe@example.com")
    b = _contact("Jane D.", email="Jane.Doe@Example.com")
    reason = contacts_match_reason(a, b)
    assert reason is not None
    assert "email" in reason


def test_same_name_different_casing_is_a_match():
    a = _contact("Jane Doe")
    b = _contact("jane doe")
    reason = contacts_match_reason(a, b)
    assert reason is not None
    assert "exact name match" in reason


def test_similar_name_typo_is_a_fuzzy_match():
    a = _contact("Carlos Mendez")
    b = _contact("Carlos Mendes")  # one-letter typo
    reason = contacts_match_reason(a, b)
    assert reason is not None


def test_different_people_do_not_match():
    a = _contact("Jane Doe")
    b = _contact("Carlos Mendez")
    assert contacts_match_reason(a, b) is None


def test_different_emails_at_different_names_do_not_match():
    a = _contact("Jane Doe", email="jane@example.com")
    b = _contact("Carlos Mendez", email="carlos@example.com")
    assert contacts_match_reason(a, b) is None
