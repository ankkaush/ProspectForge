"""Step 10: the complete fit pass, run after enrichment.

Reuses evaluate_criterion (the same engine Step 8's prefilter uses)
against the full criteria set - both phases now, since enrichment has
filled in tech_stack/funding_stage/growth_signal by this point.

Tier logic, in order:
  1. Any disqualifier (any phase) that evaluates True -> REJECTED.
  2. All Tier 1 criteria evaluate True (no failures, no unknowns) -> TIER_1.
  3. All Tier 2 criteria evaluate True -> TIER_2 (even if Tier 1 didn't
     clear - e.g. missing/failed post-enrichment data kept it out of Tier 1
     but the firmographics are still solid).
  4. Otherwise: a definite Tier 2 failure -> TIER_3 (doesn't clear the bar,
     but isn't disqualified either - a different outcome from REJECTED,
     see icp/models.py's ICPConfig docstring). Only unknowns, no failures
     -> INSUFFICIENT_DATA.

This is the "degrade gracefully" failure scenario the roadmap names for
this step: an account with missing post-enrichment data never crashes the
evaluator and is never silently dropped - it lands at TIER_2 (with a
recorded reason) or INSUFFICIENT_DATA, never treated as a hard failure
just because a field is unknown.
"""

from __future__ import annotations

from prospectforge.icp.models import ICPConfig
from prospectforge.models import Account, FitPassType, FitResult, FitTier

from .rules import evaluate_criterion, make_fit_result


def evaluate_full_fit(account: Account, icp: ICPConfig) -> FitResult:
    for disqualifier in icp.disqualifiers:
        if evaluate_criterion(account, disqualifier) is True:
            return make_fit_result(
                account.id, FitPassType.FULL, FitTier.REJECTED, [disqualifier.description]
            )

    tier_1_evals = [(c, evaluate_criterion(account, c)) for c in icp.tier_1_criteria]
    tier_1_failed = [c for c, r in tier_1_evals if r is False]
    tier_1_unknown = [c for c, r in tier_1_evals if r is None]

    if not tier_1_failed and not tier_1_unknown:
        return make_fit_result(
            account.id, FitPassType.FULL, FitTier.TIER_1, [c.description for c, _ in tier_1_evals]
        )

    tier_2_evals = [(c, evaluate_criterion(account, c)) for c in icp.tier_2_criteria]
    tier_2_failed = [c for c, r in tier_2_evals if r is False]
    tier_2_unknown = [c for c, r in tier_2_evals if r is None]

    if not tier_2_failed and not tier_2_unknown:
        reasons = [c.description for c, _ in tier_2_evals]
        if tier_1_unknown:
            reasons += [
                f"missing data for '{c.field}' prevented Tier 1: {c.description}"
                for c in tier_1_unknown
            ]
        if tier_1_failed:
            reasons += [f"did not meet Tier 1 requirement: {c.description}" for c in tier_1_failed]
        return make_fit_result(account.id, FitPassType.FULL, FitTier.TIER_2, reasons)

    if tier_2_failed:
        return make_fit_result(
            account.id, FitPassType.FULL, FitTier.TIER_3, [c.description for c in tier_2_failed]
        )

    return make_fit_result(
        account.id,
        FitPassType.FULL,
        FitTier.INSUFFICIENT_DATA,
        [f"missing data for '{c.field}': {c.description}" for c in tier_2_unknown],
    )
