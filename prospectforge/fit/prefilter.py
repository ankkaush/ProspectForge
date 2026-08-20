"""Step 8: the cheap, pre-enrichment fit pass.

Evaluates only PRE_ENRICHMENT-phase disqualifiers and Tier 2 criteria (the
widest net - see discovery/criteria.py's docstring for the same reasoning)
against an Account's already-known fields. Produces a FitResult with
pass_type=PREFILTER; the full evaluation (all criteria, including
post-enrichment ones) happens in Step 10, after enrichment.

Precedence when a criterion can't be evaluated (missing data) alongside one
that definitely fails: a definite failure always wins. Missing data on one
field doesn't get to soften a clear rejection on another - "insufficient
data" only applies when nothing has definitely failed yet.
"""

from __future__ import annotations

from prospectforge.icp.models import EnrichmentPhase, ICPConfig
from prospectforge.models import Account, FitPassType, FitResult, FitTier

from .rules import evaluate_criterion, make_fit_result


def prefilter_account(account: Account, icp: ICPConfig) -> FitResult:
    pre_disqualifiers = [d for d in icp.disqualifiers if d.phase == EnrichmentPhase.PRE_ENRICHMENT]
    for disqualifier in pre_disqualifiers:
        if evaluate_criterion(account, disqualifier) is True:
            return make_fit_result(
                account.id, FitPassType.PREFILTER, FitTier.REJECTED, [disqualifier.description]
            )

    pre_tier_2 = [c for c in icp.tier_2_criteria if c.phase == EnrichmentPhase.PRE_ENRICHMENT]
    evaluations = [(c, evaluate_criterion(account, c)) for c in pre_tier_2]

    failed = [c for c, result in evaluations if result is False]
    if failed:
        return make_fit_result(
            account.id, FitPassType.PREFILTER, FitTier.REJECTED, [c.description for c in failed]
        )

    unknown = [c for c, result in evaluations if result is None]
    if unknown:
        return make_fit_result(
            account.id,
            FitPassType.PREFILTER,
            FitTier.INSUFFICIENT_DATA,
            [f"missing data for '{c.field}': {c.description}" for c in unknown],
        )

    return make_fit_result(
        account.id, FitPassType.PREFILTER, FitTier.TIER_2, [c.description for c in pre_tier_2]
    )
