"""Tests for CsvContactEnrichmentProvider, using the real seed data
(prospectforge/contact_enrichment/seed_data/contact_enrichment.csv) -
deliberately built with a verified email, an unverified one, a malformed
one, and a not-found case, so these tests exercise real validation rather
than a synthetic fixture.
"""

import uuid

import pytest

from prospectforge.contact_enrichment.providers.csv_provider import CsvContactEnrichmentProvider
from prospectforge.models import Account, Contact


@pytest.fixture()
def provider() -> CsvContactEnrichmentProvider:
    return CsvContactEnrichmentProvider()


def _contact(name: str) -> Contact:
    return Contact(id=uuid.uuid4(), account_id=uuid.uuid4(), name=name)


def _account(domain: str) -> Account:
    return Account(domain=domain, name="Test Co")


def test_verified_email_is_returned_as_verified(provider):
    result = provider.enrich_contact(_contact("Jane Doe"), _account("northstar-metrics.com"))
    assert result.found is True
    assert result.email == "jane.doe@northstar-metrics.com"
    assert result.email_confidence == "verified"
    assert result.seniority == "vp"


def test_unverified_email_is_kept_as_unverified_not_upgraded(provider):
    """This step's core failure scenario: a low-confidence email must
    never be silently promoted to 'verified' just because a value is
    present."""

    result = provider.enrich_contact(_contact("Carlos Mendez"), _account("northstar-metrics.com"))
    assert result.email == "carlos.mendez@northstar-metrics.com"
    assert result.email_confidence == "unverified"


def test_malformed_email_is_stored_as_invalid_not_a_usable_fact(provider):
    result = provider.enrich_contact(_contact("Grace Kim"), _account("verdantanalytics.com"))
    assert result.email is None  # not passed through despite being present in the source row
    assert result.email_confidence == "invalid"
    assert result.seniority == "vp"  # other fields still come through


def test_contact_with_no_email_on_file_is_found_with_no_email(provider):
    result = provider.enrich_contact(_contact("Sam Okafor"), _account("bramblecart.com"))
    assert result.found is True
    assert result.email == "sam.okafor@bramblecart.com"


def test_contact_with_entirely_blank_row_is_found_true_no_email(provider):
    """Ahmed Farouk's row has every field blank except identity - the
    provider found *a record*, it just has no usable contact details."""

    result = provider.enrich_contact(_contact("Ahmed Farouk"), _account("quillstack.io"))
    assert result.found is True
    assert result.email is None


def test_unknown_contact_is_found_false(provider):
    result = provider.enrich_contact(_contact("Nobody Special"), _account("nowhere.com"))
    assert result.found is False


def test_missing_csv_file_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        CsvContactEnrichmentProvider(csv_path=tmp_path / "does-not-exist.csv")
