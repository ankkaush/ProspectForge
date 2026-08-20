"""Orchestrates prioritization: scores every (account, contact) pair with
a QUALIFIED verdict, upserts a ProspectRecord per pair, then assigns a
global rank across all of them.

Global, not per-run, for the same reason dedup (Step 14) scans globally:
a rep's queue should reflect every currently-qualified prospect, not just
what one run happened to touch. Re-ranking is cheap (pure DB logic, no
external calls) at this project's scale.

Uses each (account, contact) pair's MOST RECENT QualificationResult, not
every historical one - an account re-qualified in a later run shouldn't
leave stale duplicate entries in the queue; ProspectRecord is upserted
(one row per pair), not appended to.

Deliberately re-runnable against a different weighting without touching
any upstream data - the roadmap's stated exit criteria for this step.
Pass a `weights` dict to run_prioritization() and only the scores/ranks
change.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.logging import log_context
from app.orm import (
    AccountORM,
    ContactORM,
    EvidenceORM,
    FitResultORM,
    ProspectRecordORM,
    QualificationResultORM,
)
from prospectforge.models.enums import AccountStatus, FitPassType, QualificationStatus

from .scorer import DEFAULT_WEIGHTS, compute_priority_score

logger = logging.getLogger("prospectforge.prioritization")


def run_prioritization(
    run_id: uuid.UUID,
    session: Session,
    *,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        active_weights = weights or DEFAULT_WEIGHTS

        summary = {"prospects_scored": 0, "weights": dict(active_weights)}

        qualified_accounts = session.query(AccountORM).filter_by(status=AccountStatus.QUALIFIED).all()

        for account_orm in qualified_accounts:
            with log_context(account_id=str(account_orm.id)):
                fit_result = (
                    session.query(FitResultORM)
                    .filter_by(account_id=account_orm.id, pass_type=FitPassType.FULL)
                    .order_by(FitResultORM.evaluated_at.desc())
                    .first()
                )
                evidence = session.query(EvidenceORM).filter_by(account_id=account_orm.id).all()
                contacts = session.query(ContactORM).filter_by(account_id=account_orm.id).all()

                for contact_orm in contacts:
                    latest_qual = (
                        session.query(QualificationResultORM)
                        .filter_by(account_id=account_orm.id, contact_id=contact_orm.id)
                        .order_by(QualificationResultORM.evaluated_at.desc())
                        .first()
                    )
                    if latest_qual is None or latest_qual.status != QualificationStatus.QUALIFIED:
                        continue

                    score = compute_priority_score(
                        fit_result.tier if fit_result else None,
                        evidence,
                        contact_orm,
                        weights=active_weights,
                    )

                    existing = (
                        session.query(ProspectRecordORM)
                        .filter_by(account_id=account_orm.id, contact_id=contact_orm.id)
                        .one_or_none()
                    )
                    if existing is not None:
                        existing.qualification_result_id = latest_qual.id
                        existing.priority_score = score
                    else:
                        session.add(
                            ProspectRecordORM(
                                account_id=account_orm.id,
                                contact_id=contact_orm.id,
                                qualification_result_id=latest_qual.id,
                                priority_score=score,
                            )
                        )
                    summary["prospects_scored"] += 1
                    logger.info(
                        "scored prospect: domain=%s contact_id=%s score=%.3f",
                        account_orm.domain,
                        contact_orm.id,
                        score,
                    )
                    session.flush()

        _assign_global_ranks(session)

        logger.info("prioritization completed with summary=%s", summary)
        return summary


def _assign_global_ranks(session: Session) -> None:
    """Deterministic tie-break, per the roadmap's explicit test
    requirement: priority_score, then qualification confidence, then
    account domain alphabetically - never insertion order."""

    records = session.query(ProspectRecordORM).all()

    def sort_key(record: ProspectRecordORM):
        qual = session.get(QualificationResultORM, record.qualification_result_id)
        account = session.get(AccountORM, record.account_id)
        return (
            -(record.priority_score if record.priority_score is not None else 0.0),
            -(qual.confidence if qual else 0.0),
            account.domain if account else "",
        )

    for rank, record in enumerate(sorted(records, key=sort_key), start=1):
        record.priority_rank = rank

    session.flush()
