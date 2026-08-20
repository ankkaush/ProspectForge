"""Tests for run_dedup - including the roadmap's explicit exit criteria
for this step: seed two obviously-duplicate accounts, run the pass, and
get one merged record with a visible merge log.
"""

import uuid

from app.orm import AccountORM, ContactORM, EvidenceORM, ProviderRecordORM, RunORM
from prospectforge.dedup.service import run_dedup
from prospectforge.models.enums import (
    AccountStatus,
    ConfidenceLevel,
    ContactStatus,
    EvidenceSourceType,
    FitTier,
    RunStatus,
)


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def test_exit_criteria_two_obvious_duplicate_accounts_merge_into_one(db_session, caplog):
    """The roadmap's stated exit criteria for this step, verified
    directly: seed two obviously-duplicate accounts (a www. variant),
    run the pass, get one merged record with a visible merge log."""

    run = _bare_run(db_session)
    older = AccountORM(
        id=uuid.uuid4(), domain="northstar-metrics.com", name="Northstar Metrics",
        status=AccountStatus.ENRICHED, industry="Computer Software",
    )
    newer_duplicate = AccountORM(
        id=uuid.uuid4(), domain="www.northstar-metrics.com", name="Northstar Metrics",
        status=AccountStatus.RAW,
    )
    db_session.add_all([older, newer_duplicate])
    db_session.flush()

    import logging

    caplog.set_level(logging.INFO, logger="prospectforge.dedup")
    summary = run_dedup(run.id, db_session)

    assert summary["accounts_merged"] == 1
    assert len(summary["merges"]) == 1
    assert summary["merges"][0]["survivor_domain"] == "northstar-metrics.com"
    assert summary["merges"][0]["merged_domain"] == "www.northstar-metrics.com"

    remaining = db_session.query(AccountORM).filter(
        AccountORM.domain.in_(["northstar-metrics.com", "www.northstar-metrics.com"])
    ).all()
    assert len(remaining) == 1
    assert remaining[0].domain == "northstar-metrics.com"

    # visible merge log - the exit criteria's other explicit requirement
    assert any("merged account" in record.message for record in caplog.records)


def test_survivor_is_the_more_pipeline_advanced_record(db_session):
    run = _bare_run(db_session)
    less_advanced = AccountORM(
        id=uuid.uuid4(), domain="acme.com", name="Acme", status=AccountStatus.RAW
    )
    more_advanced = AccountORM(
        id=uuid.uuid4(), domain="www.acme.com", name="Acme",
        status=AccountStatus.FIT_EVALUATED, fit_tier=FitTier.TIER_1,
    )
    db_session.add_all([less_advanced, more_advanced])
    db_session.flush()

    run_dedup(run.id, db_session)

    # only one row remains - check it kept the FIT_EVALUATED status/tier,
    # not RAW, confirming the more pipeline-advanced record survived
    survivor = db_session.query(AccountORM).one()
    assert survivor.status == AccountStatus.FIT_EVALUATED
    assert survivor.fit_tier == FitTier.TIER_1


def test_merge_fills_gaps_without_overwriting_survivors_known_values(db_session):
    run = _bare_run(db_session)
    survivor_candidate = AccountORM(
        id=uuid.uuid4(), domain="acme.com", name="Acme",
        status=AccountStatus.ENRICHED, industry="Computer Software", employee_count=None,
    )
    loser_candidate = AccountORM(
        id=uuid.uuid4(), domain="www.acme.com", name="Acme",
        status=AccountStatus.RAW, industry="Should Not Overwrite", employee_count=150,
    )
    db_session.add_all([survivor_candidate, loser_candidate])
    db_session.flush()

    run_dedup(run.id, db_session)

    survivor = db_session.query(AccountORM).one()
    assert survivor.industry == "Computer Software"  # kept, not overwritten by loser
    assert survivor.employee_count == 150  # filled in from loser, since survivor had none


def test_child_rows_are_repointed_to_the_survivor_not_orphaned(db_session):
    run = _bare_run(db_session)
    survivor_candidate = AccountORM(
        id=uuid.uuid4(), domain="acme.com", name="Acme", status=AccountStatus.ENRICHED
    )
    loser_candidate = AccountORM(
        id=uuid.uuid4(), domain="www.acme.com", name="Acme", status=AccountStatus.RAW
    )
    db_session.add_all([survivor_candidate, loser_candidate])
    db_session.flush()

    contact = ContactORM(
        id=uuid.uuid4(), account_id=loser_candidate.id, name="Jane Doe",
        status=ContactStatus.DISCOVERED,
    )
    evidence = EvidenceORM(
        id=uuid.uuid4(), account_id=loser_candidate.id, claim="X",
        source_type=EvidenceSourceType.AI_INFERRED, confidence=ConfidenceLevel.LOW,
    )
    record = ProviderRecordORM(
        id=uuid.uuid4(), account_id=loser_candidate.id, provider="apollo",
        operation="discovery", payload={},
    )
    db_session.add_all([contact, evidence, record])
    db_session.flush()
    loser_id = loser_candidate.id

    run_dedup(run.id, db_session)

    survivor = db_session.query(AccountORM).one()
    assert db_session.query(ContactORM).filter_by(account_id=survivor.id).count() == 1
    assert db_session.query(EvidenceORM).filter_by(account_id=survivor.id).count() == 1
    assert db_session.query(ProviderRecordORM).filter_by(account_id=survivor.id).count() == 1
    # nothing still points at the deleted loser
    assert db_session.query(ContactORM).filter_by(account_id=loser_id).count() == 0


def test_two_genuinely_different_companies_are_never_merged(db_session):
    run = _bare_run(db_session)
    a = AccountORM(id=uuid.uuid4(), domain="northstar-metrics.com", name="Northstar Metrics")
    b = AccountORM(id=uuid.uuid4(), domain="verdantanalytics.com", name="Verdant Analytics")
    db_session.add_all([a, b])
    db_session.flush()

    summary = run_dedup(run.id, db_session)

    assert summary["accounts_merged"] == 0
    assert db_session.query(AccountORM).count() == 2


def test_similar_but_distinct_names_are_not_merged(db_session):
    """The over-aggressive-matching failure scenario this step explicitly
    guards against."""

    run = _bare_run(db_session)
    a = AccountORM(id=uuid.uuid4(), domain="northstar-metrics.com", name="Northstar Metrics")
    b = AccountORM(id=uuid.uuid4(), domain="northstar-analytics.com", name="Northstar Analytics")
    db_session.add_all([a, b])
    db_session.flush()

    summary = run_dedup(run.id, db_session)

    assert summary["accounts_merged"] == 0
    assert db_session.query(AccountORM).count() == 2


# --- contact dedup, scoped within an account --------------------------

def test_duplicate_contacts_at_the_same_account_are_merged(db_session):
    run = _bare_run(db_session)
    account = AccountORM(id=uuid.uuid4(), domain="acme.com", name="Acme")
    db_session.add(account)
    db_session.flush()

    verified = ContactORM(
        id=uuid.uuid4(), account_id=account.id, name="Jane Doe",
        email="jane.doe@acme.com", email_confidence="verified",
    )
    unverified_dupe = ContactORM(
        id=uuid.uuid4(), account_id=account.id, name="Jane D.",
        email="Jane.Doe@Acme.com", email_confidence="unverified",
    )
    db_session.add_all([verified, unverified_dupe])
    db_session.flush()

    summary = run_dedup(run.id, db_session)

    assert summary["contacts_merged"] == 1
    remaining = db_session.query(ContactORM).filter_by(account_id=account.id).all()
    assert len(remaining) == 1
    # the higher-confidence record survives
    assert remaining[0].email_confidence == "verified"


def test_same_name_contacts_at_different_accounts_are_not_merged(db_session):
    """Two different people who happen to share a name at two different
    companies are not duplicates - contact matching must be scoped per
    account, never global."""

    run = _bare_run(db_session)
    account_a = AccountORM(id=uuid.uuid4(), domain="acme.com", name="Acme")
    account_b = AccountORM(id=uuid.uuid4(), domain="beta.com", name="Beta")
    db_session.add_all([account_a, account_b])
    db_session.flush()

    contact_a = ContactORM(id=uuid.uuid4(), account_id=account_a.id, name="Jane Doe")
    contact_b = ContactORM(id=uuid.uuid4(), account_id=account_b.id, name="Jane Doe")
    db_session.add_all([contact_a, contact_b])
    db_session.flush()

    summary = run_dedup(run.id, db_session)

    assert summary["contacts_merged"] == 0
    assert db_session.query(ContactORM).count() == 2
