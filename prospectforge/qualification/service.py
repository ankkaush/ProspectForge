"""Orchestrates qualification for a run: finds accounts waiting at
status=RESEARCHED, runs the deterministic engine (engine.py - no AI) to
produce one QualificationResult per contact, then produces each result's
rationale_text.

By default (QUALIFICATION_RATIONALE_PROVIDER=deterministic, the standard
pipeline's setting), that rationale is generated entirely in Python from
the engine's own reasons (rationale.py's templated_rationale) - no
Anthropic call is made, and none is required for qualification to run.
An AI-assisted rationale provider (providers/anthropic_rationale.py) can
be opted into instead (QUALIFICATION_RATIONALE_PROVIDER=anthropic, or
passing an explicit `provider=` to run_qualification) purely to phrase
the same, already-decided result more naturally - it never decides
anything.

The rationale step is never allowed to affect the qualification verdict
itself - by the time it's called, status/reasons/confidence are already
final and persisted-in-memory. When the optional AI provider is used, a
rationale-generation failure changes only which sentence ends up in
rationale_text (falling back to the same templated text), never
QualificationStatus. This is why rationale failures don't need per-item
RetryableError handling that fails the item the way enrichment/research
do - there's nothing to fail; there's only a better or worse sentence.

Same resumability pattern as every other stage: queries by
Account.status, not by which run researched the account.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.orm import AccountORM, ContactORM, EvidenceORM, FitResultORM, QualificationResultORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import AccountStatus, FitPassType, QualificationStatus

from .engine import qualify_account
from .interface import EvidenceSummary, RationaleContext, RationaleProvider
from .rationale import templated_rationale

logger = logging.getLogger("prospectforge.qualification")


def resolve_rationale_provider(setting: str, anthropic_api_key: str) -> Optional[RationaleProvider]:
    """Pure branch logic, kept separate from get_default_rationale_provider()
    so the "which provider for which setting" decision is testable without
    reaching through Settings/env vars.

    Returns None for "deterministic" - the caller's signal to skip the AI
    provider entirely and go straight to the templated rationale."""

    if setting == "deterministic":
        return None
    if setting == "anthropic":
        from .providers.anthropic_rationale import AnthropicRationaleProvider

        if not anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set - required when "
                "QUALIFICATION_RATIONALE_PROVIDER=anthropic. Set it in .env, switch "
                "QUALIFICATION_RATIONALE_PROVIDER back to 'deterministic' to run "
                "without Anthropic, or pass an explicit rationale_provider to "
                "run_qualification() for testing."
            )
        return AnthropicRationaleProvider(api_key=anthropic_api_key)
    raise ValueError(f"Unknown qualification_rationale_provider setting: {setting!r}")


def get_default_rationale_provider() -> Optional[RationaleProvider]:
    settings = get_settings()
    return resolve_rationale_provider(settings.qualification_rationale_provider, settings.anthropic_api_key)


def run_qualification(
    run_id: uuid.UUID,
    session: Session,
    *,
    provider: Optional[RationaleProvider] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        active_provider = provider or get_default_rationale_provider()

        summary = {
            "accounts_evaluated": 0,
            "accounts_qualified": 0,
            "accounts_not_qualified": 0,
            "qualification_results": 0,
            "rationale_generated": 0,
            "rationale_deterministic": 0,
            "rationale_fallback": 0,
        }

        accounts = session.query(AccountORM).filter_by(status=AccountStatus.RESEARCHED).all()

        for account_orm in accounts:
            with log_context(account_id=str(account_orm.id)):
                summary["accounts_evaluated"] += 1

                fit_result = (
                    session.query(FitResultORM)
                    .filter_by(account_id=account_orm.id, pass_type=FitPassType.FULL)
                    .order_by(FitResultORM.evaluated_at.desc())
                    .first()
                )
                evidence = session.query(EvidenceORM).filter_by(account_id=account_orm.id).all()
                contacts = session.query(ContactORM).filter_by(account_id=account_orm.id).all()

                det_results = qualify_account(
                    account_id=account_orm.id,
                    fit_tier=fit_result.tier if fit_result else None,
                    fit_reasons=fit_result.reasons if fit_result else [],
                    evidence=evidence,
                    contacts=contacts,
                )

                fit_tier_label = fit_result.tier.value if fit_result else "unknown"

                any_qualified = False
                for det in det_results:
                    rationale_text = _generate_rationale_or_fallback(
                        active_provider,
                        account_orm,
                        fit_tier_label,
                        det,
                        evidence,
                        contacts,
                        session,
                        run_id,
                        summary,
                    )

                    session.add(
                        QualificationResultORM(
                            account_id=det.account_id,
                            contact_id=det.contact_id,
                            status=det.status,
                            reasons=det.reasons,
                            confidence=det.confidence,
                            evidence_ids=[str(eid) for eid in det.evidence_ids],
                            rationale_text=rationale_text,
                        )
                    )
                    summary["qualification_results"] += 1
                    if det.status == QualificationStatus.QUALIFIED:
                        any_qualified = True

                from app.mappers import orm_to_account

                account = orm_to_account(account_orm)
                account.transition_to(
                    AccountStatus.QUALIFIED if any_qualified else AccountStatus.NOT_QUALIFIED
                )
                account_orm.status = account.status
                if any_qualified:
                    summary["accounts_qualified"] += 1
                else:
                    summary["accounts_not_qualified"] += 1

                logger.info(
                    "qualified account domain=%s -> account_status=%s (%d qualification result(s))",
                    account_orm.domain,
                    account_orm.status.value,
                    len(det_results),
                )
                session.flush()

        logger.info("qualification completed with summary=%s", summary)
        return summary


def _generate_rationale_or_fallback(
    provider: Optional[RationaleProvider],
    account_orm: AccountORM,
    fit_tier_label: str,
    det,
    evidence,
    contacts,
    session: Session,
    run_id: uuid.UUID,
    summary: Dict[str, Any],
) -> str:
    if provider is None:
        # The standard pipeline's path: no AI provider configured at all,
        # so there's no external call to attempt or log - go straight to
        # the deterministic rationale built from the engine's own reasons.
        summary["rationale_deterministic"] += 1
        return templated_rationale(det.reasons)

    contact_orm = next((c for c in contacts if c.id == det.contact_id), None)

    context = RationaleContext(
        account_name=account_orm.name,
        fit_tier=fit_tier_label,
        deterministic_reasons=det.reasons,
        evidence=[
            EvidenceSummary(id=e.id, claim=e.claim, confidence=e.confidence.value) for e in evidence
        ],
        contact_name=contact_orm.name if contact_orm else None,
        contact_title=contact_orm.title if contact_orm else None,
        contact_email_confidence=contact_orm.email_confidence if contact_orm else None,
    )

    try:
        result = call_with_retry(
            lambda: provider.generate_rationale(context),
            session=session,
            run_id=run_id,
            account_id=account_orm.id,
            contact_id=det.contact_id,
            provider="anthropic",
            operation="qualification_rationale",
        )
    except (RetryableError, NonRetryableError) as exc:
        logger.info("rationale generation failed for account_id=%s: %s", account_orm.id, exc)
        summary["rationale_fallback"] += 1
        return templated_rationale(det.reasons)

    if result.rationale_text is None:
        summary["rationale_fallback"] += 1
        return templated_rationale(det.reasons)

    summary["rationale_generated"] += 1
    return result.rationale_text
