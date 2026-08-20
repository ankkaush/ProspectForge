"""The ICP config schema - what a criterion, a disqualifier, and a full ICP
actually look like as data.

Deliberately does NOT contain any evaluation logic (no "does this account
pass" method). That's Step 8/10's job (fit/prefilter.py and
fit/evaluator.py) - this module only defines the shape of the config those
steps will read. Keeping schema and evaluation separate means the ICP can
be fully designed, validated, and reviewed (this step) before a single line
of matching logic exists.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List

from pydantic import BaseModel, Field, field_validator


class EnrichmentPhase(str, Enum):
    """Which of the two fit passes (Step 8's cheap prefilter, or Step 10's
    full evaluation) a criterion belongs to - determined by whether the
    field it checks is available straight from discovery, or only after
    enrichment. See Step 2's methodology notes for why this split matters:
    evaluating a post-enrichment field during the cheap prefilter would
    mean judging accounts on data we don't have yet."""

    PRE_ENRICHMENT = "pre_enrichment"
    POST_ENRICHMENT = "post_enrichment"


class CriterionCategory(str, Enum):
    """The criteria family, per Step 2's framework - kept on each
    criterion so a FitResult's reasons can later be grouped/explained by
    category, not just as an unlabeled list of strings."""

    FIRMOGRAPHIC = "firmographic"
    TECHNOGRAPHIC = "technographic"
    BEHAVIORAL = "behavioral"


class CriterionOperator(str, Enum):
    EQUALS = "equals"
    IN = "in"
    CONTAINS = "contains"  # for list fields like tech_stack, or substring match on text
    GTE = "gte"
    LTE = "lte"
    BETWEEN = "between"


# The single source of truth for which Account fields are known before
# enrichment vs. only after. The loader checks every criterion's declared
# `phase` against this map - a criterion that claims a post-enrichment
# field (e.g. tech_stack) is available pre-enrichment is a config bug, not
# a matter of opinion, and is rejected at load time rather than silently
# misleading Step 8's cheap filter.
ACCOUNT_FIELD_PHASES: dict[str, EnrichmentPhase] = {
    "industry": EnrichmentPhase.PRE_ENRICHMENT,
    "employee_count": EnrichmentPhase.PRE_ENRICHMENT,
    "geography": EnrichmentPhase.PRE_ENRICHMENT,
    "tech_stack": EnrichmentPhase.POST_ENRICHMENT,
    "funding_stage": EnrichmentPhase.POST_ENRICHMENT,
    "growth_signal": EnrichmentPhase.POST_ENRICHMENT,
}


class Criterion(BaseModel):
    field: str
    operator: CriterionOperator
    value: Any
    phase: EnrichmentPhase
    category: CriterionCategory
    description: str  # human-readable reason, surfaced later in FitResult.reasons

    @field_validator("field")
    @classmethod
    def field_must_be_a_known_account_field(cls, v: str) -> str:
        if v not in ACCOUNT_FIELD_PHASES:
            raise ValueError(
                f"'{v}' is not a recognized Account field. Known fields: "
                f"{sorted(ACCOUNT_FIELD_PHASES)}"
            )
        return v


class Disqualifier(BaseModel):
    """A hard 'no,' independent of the tier criteria - see the module
    docstring and Step 2 notes for why this is a separate list rather than
    the logical inverse of a positive criterion."""

    field: str
    operator: CriterionOperator
    value: Any
    phase: EnrichmentPhase
    description: str

    @field_validator("field")
    @classmethod
    def field_must_be_a_known_account_field(cls, v: str) -> str:
        if v not in ACCOUNT_FIELD_PHASES:
            raise ValueError(
                f"'{v}' is not a recognized Account field. Known fields: "
                f"{sorted(ACCOUNT_FIELD_PHASES)}"
            )
        return v


class ICPConfig(BaseModel):
    id: str
    version: int
    name: str
    description: str

    # Tier 1 requires every criterion in tier_1_criteria to match.
    # Tier 2 requires every criterion in tier_2_criteria to match (typically
    # a relaxed subset of tier 1 - see the seed config for this project's
    # actual reasoning). An account that fails tier_2_criteria but has no
    # disqualifier hit falls to Tier 3 by default (evaluated in Step 8/10,
    # not here).
    tier_1_criteria: List[Criterion] = Field(default_factory=list)
    tier_2_criteria: List[Criterion] = Field(default_factory=list)

    disqualifiers: List[Disqualifier] = Field(default_factory=list)
