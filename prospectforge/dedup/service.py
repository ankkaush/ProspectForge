"""Orchestrates the deep dedup pass: scans the whole accounts table (and,
per surviving account, its contacts) for near-duplicates the exact-match
ingestion-time check (Step 7) wouldn't have caught, merges them, and
reports what happened.

Merge semantics - the roadmap's explicit requirement ("preserves the
highest-confidence value per field, not just the newest"):
  - The SURVIVOR is chosen by pipeline progress (a record further along
    the state machine has been more thoroughly vetted - see STATUS_RANK),
    tie-broken by which record is older (first-known).
  - Field values are merged, not overwritten wholesale: the survivor keeps
    its own non-null values; a null field is filled in from the loser if
    the loser has it. The loser never clobbers a value the survivor
    already has.
  - Every child row (contacts, fit_results, evidence, provider_records,
    external_call_attempts) referencing the loser is re-pointed to the
    survivor before the loser is deleted - nothing referencing the loser
    is silently orphaned or lost.

Audit trail: every merge is logged (structured, greppable - the same
mechanism every other stage in this project already uses) and returned in
the run summary as a list of {survivor, merged, reason} entries. No
separate MergeLog table - see ADR-010 for why that would be schema
complexity without a clear payoff here.

Scans the WHOLE dataset each time, not just accounts touched in the
current run - a near-duplicate can come from two different runs, and this
stage is pure DB logic (no external calls), so re-scanning everything is
cheap at this project's scale. O(n^2) pairwise comparison - a known,
accepted limit for a dataset of dozens to low hundreds of records, not
thousands.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

import logging

from sqlalchemy.orm import Session

from app.logging import log_context
from app.orm import (
    AccountORM,
    ContactORM,
    EvidenceORM,
    ExternalCallAttemptORM,
    FitResultORM,
    ProviderRecordORM,
)
from prospectforge.models.enums import AccountStatus

from .matchers import accounts_match_reason, contacts_match_reason

logger = logging.getLogger("prospectforge.dedup")

STATUS_RANK = {
    AccountStatus.RAW: 0,
    AccountStatus.ADVANCED: 1,
    AccountStatus.REJECTED_EARLY: 1,
    AccountStatus.ENRICHED: 2,
    AccountStatus.ENRICHMENT_FAILED: 2,
    AccountStatus.FIT_EVALUATED: 3,
    AccountStatus.RESEARCHED: 4,
    AccountStatus.RESEARCH_FAILED: 4,
    AccountStatus.REJECTED: 4,
    AccountStatus.QUALIFIED: 5,
    AccountStatus.NOT_QUALIFIED: 5,
    AccountStatus.REVIEWED: 6,
    AccountStatus.SYNCED: 7,
}

_ACCOUNT_MERGE_FIELDS = [
    "industry",
    "employee_count",
    "geography",
    "tech_stack",
    "funding_stage",
    "growth_signal",
]

_EMAIL_CONFIDENCE_RANK = {"verified": 2, "unverified": 1, "invalid": 0, None: -1}


def run_dedup(run_id: uuid.UUID, session: Session) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        summary = {"accounts_merged": 0, "contacts_merged": 0, "merges": []}

        _dedup_accounts(session, summary)
        _dedup_contacts(session, summary)

        logger.info(
            "dedup completed: accounts_merged=%d contacts_merged=%d",
            summary["accounts_merged"],
            summary["contacts_merged"],
        )
        return summary


def _dedup_accounts(session: Session, summary: Dict[str, Any]) -> None:
    accounts = session.query(AccountORM).order_by(AccountORM.created_at).all()

    i = 0
    while i < len(accounts):
        merged_this_round = False
        for j in range(i + 1, len(accounts)):
            reason = accounts_match_reason(accounts[i], accounts[j])
            if reason is None:
                continue

            survivor, loser = _choose_account_survivor(accounts[i], accounts[j])
            _merge_account_fields(survivor, loser)
            _repoint_account_children(session, loser_id=loser.id, survivor_id=survivor.id)

            merge_entry = {
                "survivor_domain": survivor.domain,
                "merged_domain": loser.domain,
                "reason": reason,
            }
            summary["merges"].append(merge_entry)
            summary["accounts_merged"] += 1
            logger.info(
                "merged account: survivor=%s merged=%s reason=%s",
                survivor.domain,
                loser.domain,
                reason,
            )

            session.delete(loser)
            session.flush()
            accounts = [a for a in accounts if a.id != loser.id]
            merged_this_round = True
            break

        if not merged_this_round:
            i += 1


def _choose_account_survivor(a: AccountORM, b: AccountORM):
    rank_a, rank_b = STATUS_RANK.get(a.status, 0), STATUS_RANK.get(b.status, 0)
    if rank_a != rank_b:
        survivor = a if rank_a > rank_b else b
    else:
        survivor = a if a.created_at <= b.created_at else b
    loser = b if survivor is a else a
    return survivor, loser


def _merge_account_fields(survivor: AccountORM, loser: AccountORM) -> None:
    for field in _ACCOUNT_MERGE_FIELDS:
        if getattr(survivor, field) is None and getattr(loser, field) is not None:
            setattr(survivor, field, getattr(loser, field))
    if survivor.fit_tier is None and loser.fit_tier is not None:
        survivor.fit_tier = loser.fit_tier


def _repoint_account_children(session: Session, *, loser_id, survivor_id) -> None:
    # QualificationResultORM/ProspectRecordORM aren't included - nothing
    # populates them yet (Step 15+). If dedup ever needs to run after
    # qualification exists, this list needs those two added.
    for model in (ContactORM, FitResultORM, EvidenceORM, ProviderRecordORM, ExternalCallAttemptORM):
        session.query(model).filter_by(account_id=loser_id).update({"account_id": survivor_id})
    session.flush()


def _dedup_contacts(session: Session, summary: Dict[str, Any]) -> None:
    account_ids = [row[0] for row in session.query(AccountORM.id).all()]

    for account_id in account_ids:
        contacts = (
            session.query(ContactORM)
            .filter_by(account_id=account_id)
            .order_by(ContactORM.created_at)
            .all()
        )

        i = 0
        while i < len(contacts):
            merged_this_round = False
            for j in range(i + 1, len(contacts)):
                reason = contacts_match_reason(contacts[i], contacts[j])
                if reason is None:
                    continue

                survivor, loser = _choose_contact_survivor(contacts[i], contacts[j])
                _merge_contact_fields(survivor, loser)
                _repoint_contact_children(session, loser_id=loser.id, survivor_id=survivor.id)

                summary["merges"].append(
                    {"survivor_contact": survivor.name, "merged_contact": loser.name, "reason": reason}
                )
                summary["contacts_merged"] += 1
                logger.info(
                    "merged contact: survivor_id=%s merged_id=%s reason=%s",
                    survivor.id,
                    loser.id,
                    reason,
                )

                session.delete(loser)
                session.flush()
                contacts = [c for c in contacts if c.id != loser.id]
                merged_this_round = True
                break

            if not merged_this_round:
                i += 1


def _choose_contact_survivor(a: ContactORM, b: ContactORM):
    # More complete email confidence wins; ties broken by earlier record.
    rank_a = _EMAIL_CONFIDENCE_RANK.get(a.email_confidence, -1)
    rank_b = _EMAIL_CONFIDENCE_RANK.get(b.email_confidence, -1)
    if rank_a != rank_b:
        survivor = a if rank_a > rank_b else b
    else:
        survivor = a if a.created_at <= b.created_at else b
    loser = b if survivor is a else a
    return survivor, loser


def _merge_contact_fields(survivor: ContactORM, loser: ContactORM) -> None:
    for field in ["title", "seniority", "department", "linkedin_url"]:
        if getattr(survivor, field) is None and getattr(loser, field) is not None:
            setattr(survivor, field, getattr(loser, field))
    if survivor.email is None and loser.email is not None:
        survivor.email = loser.email
        survivor.email_confidence = loser.email_confidence


def _repoint_contact_children(session: Session, *, loser_id, survivor_id) -> None:
    for model in (EvidenceORM, ProviderRecordORM, ExternalCallAttemptORM):
        session.query(model).filter_by(contact_id=loser_id).update({"contact_id": survivor_id})
    session.flush()
