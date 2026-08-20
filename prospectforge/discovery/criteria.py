"""Builds provider-independent DiscoveryCriteria from an ICPConfig's
pre-enrichment criteria (Step 6).

Reads from tier_2_criteria first, then fills in anything only present in
tier_1_criteria - tier 2 is the deliberately relaxed tier, so its
pre-enrichment values are the widest net we should cast. Casting discovery
around tier_1's (potentially narrower) values risks excluding companies
that would still be worth advancing as Tier 2 candidates. In this
project's seed config the two tiers' pre-enrichment values happen to be
identical, but the principle holds generally.
"""

from __future__ import annotations

from prospectforge.icp.models import Criterion, EnrichmentPhase, ICPConfig

from .interface import DiscoveryCriteria


def criteria_from_icp(icp: ICPConfig) -> DiscoveryCriteria:
    by_field: dict[str, Criterion] = {}

    for criterion in icp.tier_1_criteria:
        if criterion.phase == EnrichmentPhase.PRE_ENRICHMENT:
            by_field[criterion.field] = criterion

    for criterion in icp.tier_2_criteria:
        if criterion.phase == EnrichmentPhase.PRE_ENRICHMENT:
            by_field[criterion.field] = criterion  # tier 2 wins - see module docstring

    result = DiscoveryCriteria()

    industry_criterion = by_field.get("industry")
    if industry_criterion is not None:
        result.industries = list(industry_criterion.value)

    geography_criterion = by_field.get("geography")
    if geography_criterion is not None:
        result.geographies = list(geography_criterion.value)

    employee_criterion = by_field.get("employee_count")
    if employee_criterion is not None:
        # only the `between` operator is meaningful for a range - other
        # operators on employee_count (gte/lte) aren't used by this
        # project's seed config, but are handled defensively rather than
        # assumed away
        if employee_criterion.operator.value == "between":
            result.employee_count_min, result.employee_count_max = employee_criterion.value
        elif employee_criterion.operator.value == "gte":
            result.employee_count_min = employee_criterion.value
        elif employee_criterion.operator.value == "lte":
            result.employee_count_max = employee_criterion.value

    return result
