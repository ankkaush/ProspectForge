"""Transparent, weighted scoring - fully deterministic, per the roadmap's
explicit guidance for this step. Three named dimensions (fit tier, evidence
strength/recency, contact seniority), each independently computed as a
score in [0, 1], combined via configurable weights.

Guard against the named failure scenario ("a single dominant weight
silently drowns out the others"): DEFAULT_WEIGHTS deliberately caps every
dimension below 0.5, and every component score is itself capped to [0, 1]
before weighting - no dimension can contribute more than its own weight,
and no weight in the default configuration is large enough to make the
other two dimensions irrelevant. Passing a different `weights` dict
re-runs the exact same scoring against the exact same underlying data -
the roadmap's stated exit criteria for this step.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.orm import ContactORM, EvidenceORM
from prospectforge.models.enums import ConfidenceLevel, FitTier

DEFAULT_WEIGHTS: Dict[str, float] = {"fit": 0.40, "evidence": 0.35, "contact_seniority": 0.25}

_FIT_TIER_SCORES = {
    FitTier.TIER_1: 1.0,
    FitTier.TIER_2: 0.6,
    FitTier.INSUFFICIENT_DATA: 0.3,
}

_EVIDENCE_CONFIDENCE_WEIGHTS = {
    ConfidenceLevel.HIGH: 1.0,
    ConfidenceLevel.MEDIUM: 0.6,
    ConfidenceLevel.LOW: 0.3,
}

# A simple staircase, not a continuous decay curve - easy to explain to a
# rep ("this signal is within the last month" beats a precise-sounding but
# opaque formula), and easy to test.
_RECENCY_BRACKETS = [(30, 1.0), (90, 0.7), (180, 0.4)]
_RECENCY_STALE_SCORE = 0.2


def validate_weights(weights: Dict[str, float]) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Prioritization weights must sum to 1.0, got {total}")
    if any(w < 0 for w in weights.values()):
        raise ValueError("Prioritization weights must be non-negative")


def fit_tier_score(fit_tier: Optional[FitTier]) -> float:
    return _FIT_TIER_SCORES.get(fit_tier, 0.0)


def _recency_multiplier(extracted_at: datetime, *, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    if extracted_at.tzinfo is None:
        extracted_at = extracted_at.replace(tzinfo=timezone.utc)
    age_days = (now - extracted_at).days
    for max_age, multiplier in _RECENCY_BRACKETS:
        if age_days <= max_age:
            return multiplier
    return _RECENCY_STALE_SCORE


def evidence_score(evidence: List[EvidenceORM], *, now: Optional[datetime] = None) -> float:
    """The single strongest, freshest evidence item drives this score -
    one compelling, recent signal matters more than a pile of stale or
    low-confidence ones. Zero evidence scores 0.0, not a penalty beyond
    that - Step 15 already established that missing evidence doesn't
    disqualify; here it just contributes nothing to priority."""

    if not evidence:
        return 0.0
    return max(
        _EVIDENCE_CONFIDENCE_WEIGHTS.get(e.confidence, 0.3) * _recency_multiplier(e.extracted_at, now=now)
        for e in evidence
    )


# Checked in this order deliberately: "vp"/"vice president" must be
# tested before the c-suite check, or "Vice President" would match the
# c-suite bucket's "president" keyword.
#
# Matched with word boundaries (see _contains_any below), not plain
# substring search - a real bug caught by testing: "director" contains the
# literal substring "cto" ("di-RECTO-r"), which plain `in` matching was
# misclassifying as a C-suite title. Short abbreviations like "cto"/"vp"
# are exactly the risky case for this, so every bucket goes through the
# same word-boundary check for consistency.
_VP_KEYWORDS = ["vice president", "vp", "svp", "evp"]
_C_SUITE_KEYWORDS = ["chief", "ceo", "cfo", "coo", "cto", "president", "founder"]
_DIRECTOR_KEYWORDS = ["director", "head of"]
_MANAGER_KEYWORDS = ["manager", "lead"]


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in keywords)


def contact_seniority_score(title: Optional[str]) -> float:
    if not title:
        return 0.3
    title_lower = title.lower()
    if _contains_any(title_lower, _VP_KEYWORDS):
        return 0.8
    if _contains_any(title_lower, _C_SUITE_KEYWORDS):
        return 1.0
    if _contains_any(title_lower, _DIRECTOR_KEYWORDS):
        return 0.6
    if _contains_any(title_lower, _MANAGER_KEYWORDS):
        return 0.4
    return 0.3  # a real title, but not one we recognize a seniority signal in


def compute_priority_score(
    fit_tier: Optional[FitTier],
    evidence: List[EvidenceORM],
    contact: Optional[ContactORM],
    *,
    weights: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
) -> float:
    weights = weights or DEFAULT_WEIGHTS
    validate_weights(weights)

    fit_component = fit_tier_score(fit_tier)
    evidence_component = evidence_score(evidence, now=now)
    contact_component = contact_seniority_score(contact.title if contact else None)

    return (
        weights["fit"] * fit_component
        + weights["evidence"] * evidence_component
        + weights["contact_seniority"] * contact_component
    )
