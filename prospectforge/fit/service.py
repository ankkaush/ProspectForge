"""Orchestrates both fit passes: Step 8's cheap prefilter (status=RAW ->
ADVANCED/REJECTED_EARLY) and Step 10's full evaluation (status=ENRICHED ->
FIT_EVALUATED, always - see evaluator.py's module docstring for why the
tier outcome doesn't determine the status transition here).

Both query by status, not by "accounts discovered/enriched in this run" -
the resumability pattern from Step 5/7: any account sitting at the right
status, regardless of which run got it there, is fair game for this stage
to pick up.

No external calls in either pass - this is pure logic against
already-persisted data, so there's no retry utility involved. Same
per-item persistence discipline as discovery, though: each account's
FitResult and status update is flushed immediately, not batched.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.logging import log_context
from app.mappers import orm_to_account
from app.orm import AccountORM, FitResultORM
from prospectforge.icp.loader import load_icp_config
from prospectforge.models.enums import AccountStatus, FitTier

from .evaluator import evaluate_full_fit
from .prefilter import prefilter_account

logger = logging.getLogger("prospectforge.fit")


def run_prefilter(run_id: uuid.UUID, icp_config_id: str, session: Session) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        icp = load_icp_config(icp_config_id)

        summary = {
            "evaluated": 0,
            "advanced": 0,
            "rejected_early": 0,
            "insufficient_data": 0,
        }

        raw_accounts = session.query(AccountORM).filter_by(status=AccountStatus.RAW).all()

        for account_orm in raw_accounts:
            with log_context(account_id=str(account_orm.id)):
                account = orm_to_account(account_orm)
                fit_result = prefilter_account(account, icp)

                session.add(
                    FitResultORM(
                        account_id=fit_result.account_id,
                        pass_type=fit_result.pass_type,
                        tier=fit_result.tier,
                        reasons=fit_result.reasons,
                    )
                )

                if fit_result.tier == FitTier.REJECTED:
                    account.transition_to(AccountStatus.REJECTED_EARLY)
                    summary["rejected_early"] += 1
                else:
                    # TIER_2 (clean pass) and INSUFFICIENT_DATA both advance
                    # - see prefilter.py's docstring for why missing data
                    # doesn't mean automatic rejection.
                    account.transition_to(AccountStatus.ADVANCED)
                    summary["advanced"] += 1
                    if fit_result.tier == FitTier.INSUFFICIENT_DATA:
                        summary["insufficient_data"] += 1
                # account.transition_to() is the enforcement point for the
                # state machine (Step 4) - routed through here rather than
                # setting account_orm.status directly, so an illegal
                # transition would raise instead of silently writing.
                account_orm.status = account.status

                summary["evaluated"] += 1
                logger.info(
                    "prefiltered account domain=%s -> tier=%s reasons=%s",
                    account_orm.domain,
                    fit_result.tier.value,
                    fit_result.reasons,
                )
                session.flush()

        logger.info("prefilter completed with summary=%s", summary)
        return summary


def run_full_evaluation(run_id: uuid.UUID, icp_config_id: str, session: Session) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        icp = load_icp_config(icp_config_id)

        summary = {
            "evaluated": 0,
            "tier_1": 0,
            "tier_2": 0,
            "tier_3": 0,
            "rejected": 0,
            "insufficient_data": 0,
        }

        enriched_accounts = session.query(AccountORM).filter_by(status=AccountStatus.ENRICHED).all()

        for account_orm in enriched_accounts:
            with log_context(account_id=str(account_orm.id)):
                account = orm_to_account(account_orm)
                fit_result = evaluate_full_fit(account, icp)

                session.add(
                    FitResultORM(
                        account_id=fit_result.account_id,
                        pass_type=fit_result.pass_type,
                        tier=fit_result.tier,
                        reasons=fit_result.reasons,
                    )
                )

                # Every enriched account becomes FIT_EVALUATED, regardless
                # of tier - the state machine has no direct ENRICHED ->
                # REJECTED transition on purpose (see evaluator.py). Status
                # tracks "which stage has this account passed through";
                # tier (just persisted above) tracks the substantive
                # verdict. What happens to a low-tier account is Step 11's
                # decision, not this one.
                account.transition_to(AccountStatus.FIT_EVALUATED)
                account_orm.status = account.status
                account_orm.fit_tier = fit_result.tier

                summary["evaluated"] += 1
                summary[fit_result.tier.value] += 1
                logger.info(
                    "fully evaluated account domain=%s -> tier=%s reasons=%s",
                    account_orm.domain,
                    fit_result.tier.value,
                    fit_result.reasons,
                )
                session.flush()

        logger.info("full evaluation completed with summary=%s", summary)
        return summary
