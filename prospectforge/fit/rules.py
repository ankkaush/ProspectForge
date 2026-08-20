"""Generic criterion-evaluation engine, shared by Step 8's cheap prefilter
and Step 10's full evaluator - one rule-matching implementation, used
against a smaller field set now and a complete one later, per the
roadmap's explicit note not to duplicate this logic.

The central design decision: evaluate_criterion returns Optional[bool], not
bool. True/False are ordinary pass/fail. None means "can't tell - the
account doesn't have this field yet." Collapsing that third case into
False would silently treat "we don't know" as "it fails," which is exactly
the over-aggressive-filtering failure scenario the roadmap warns about for
this step.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional, Union

from prospectforge.icp.models import Criterion, CriterionOperator, Disqualifier
from prospectforge.models import Account, FitPassType, FitResult, FitTier


def get_field_value(account: Account, field: str) -> Any:
    return getattr(account, field, None)


def evaluate_criterion(account: Account, criterion: Union[Criterion, Disqualifier]) -> Optional[bool]:
    """Returns True (matches), False (doesn't match), or None (the
    account's field is unset - insufficient data to evaluate this
    criterion at all)."""

    field_value = get_field_value(account, criterion.field)
    if field_value is None:
        return None

    operator = criterion.operator
    target = criterion.value

    if operator == CriterionOperator.EQUALS:
        return field_value == target

    if operator == CriterionOperator.IN:
        return field_value in target

    if operator == CriterionOperator.CONTAINS:
        # List-valued account field (e.g. tech_stack): "contains" means at
        # least one overlap with the criterion's value list - not that
        # every value in the criterion must be present. See Step 6's
        # models.py docstring for this same semantic.
        if isinstance(field_value, (list, tuple, set)):
            return bool(set(target) & set(field_value))
        # String-valued field: substring-style match against any of the
        # criterion's target values.
        return any(str(t) in str(field_value) for t in target)

    if operator == CriterionOperator.GTE:
        return field_value >= target

    if operator == CriterionOperator.LTE:
        return field_value <= target

    if operator == CriterionOperator.BETWEEN:
        lo, hi = target
        return lo <= field_value <= hi

    raise ValueError(f"Unhandled criterion operator: {operator}")  # pragma: no cover


def make_fit_result(
    account_id: uuid.UUID, pass_type: FitPassType, tier: FitTier, reasons: List[str]
) -> FitResult:
    """Shared constructor used by both the prefilter (Step 8) and the full
    evaluator (Step 10) - one place building FitResult objects, so the two
    passes stay structurally identical apart from which criteria they
    check."""

    return FitResult(account_id=account_id, pass_type=pass_type, tier=tier, reasons=reasons)
