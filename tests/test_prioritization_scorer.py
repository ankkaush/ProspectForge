import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.orm import ContactORM, EvidenceORM
from prospectforge.models.enums import ConfidenceLevel, EvidenceSourceType, FitTier
from prospectforge.prioritization.scorer import (
    DEFAULT_WEIGHTS,
    compute_priority_score,
    contact_seniority_score,
    evidence_score,
    fit_tier_score,
    validate_weights,
)

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _evidence(confidence=ConfidenceLevel.HIGH, days_old=0) -> EvidenceORM:
    return EvidenceORM(
        id=uuid.uuid4(), account_id=uuid.uuid4(), claim="X",
        source_type=EvidenceSourceType.AI_INFERRED, confidence=confidence,
        extracted_at=NOW - timedelta(days=days_old),
    )


def _contact(title=None) -> ContactORM:
    return ContactORM(id=uuid.uuid4(), account_id=uuid.uuid4(), name="X", title=title)


# --- validate_weights --------------------------------------------------

def test_default_weights_sum_to_one():
    validate_weights(DEFAULT_WEIGHTS)  # no exception


def test_weights_not_summing_to_one_are_rejected():
    with pytest.raises(ValueError, match="sum to 1.0"):
        validate_weights({"fit": 0.5, "evidence": 0.5, "contact_seniority": 0.5})


def test_negative_weight_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        validate_weights({"fit": 1.5, "evidence": -0.5, "contact_seniority": 0.0})


def test_no_default_weight_exceeds_half():
    """The concrete guard against the named failure scenario - no single
    dimension is configured heavily enough, by default, to make the
    others irrelevant."""

    assert all(w <= 0.5 for w in DEFAULT_WEIGHTS.values())


# --- fit_tier_score ------------------------------------------------------

def test_fit_tier_scores_are_ordered_tier1_above_tier2_above_insufficient():
    assert fit_tier_score(FitTier.TIER_1) > fit_tier_score(FitTier.TIER_2)
    assert fit_tier_score(FitTier.TIER_2) > fit_tier_score(FitTier.INSUFFICIENT_DATA)


def test_unknown_or_missing_tier_scores_zero():
    assert fit_tier_score(None) == 0.0
    assert fit_tier_score(FitTier.REJECTED) == 0.0


# --- evidence_score: strength AND recency both matter -----------------------

def test_no_evidence_scores_zero():
    assert evidence_score([], now=NOW) == 0.0


def test_fresher_evidence_outranks_stale_evidence_of_the_same_confidence():
    fresh = evidence_score([_evidence(ConfidenceLevel.HIGH, days_old=5)], now=NOW)
    stale = evidence_score([_evidence(ConfidenceLevel.HIGH, days_old=300)], now=NOW)
    assert fresh > stale


def test_higher_confidence_outranks_lower_confidence_at_the_same_age():
    high = evidence_score([_evidence(ConfidenceLevel.HIGH, days_old=10)], now=NOW)
    low = evidence_score([_evidence(ConfidenceLevel.LOW, days_old=10)], now=NOW)
    assert high > low


def test_strongest_single_item_drives_the_score_not_the_count():
    one_strong = evidence_score([_evidence(ConfidenceLevel.HIGH, days_old=1)], now=NOW)
    many_weak = evidence_score(
        [_evidence(ConfidenceLevel.LOW, days_old=200) for _ in range(5)], now=NOW
    )
    assert one_strong > many_weak


# --- contact_seniority_score -------------------------------------------------

def test_c_suite_scores_highest():
    assert contact_seniority_score("Chief Revenue Officer") == 1.0
    assert contact_seniority_score("Founder") == 1.0


def test_vp_scores_below_c_suite_but_above_director():
    vp = contact_seniority_score("VP of Sales")
    director = contact_seniority_score("Director of Sales Operations")
    c_suite = contact_seniority_score("Chief Technology Officer")
    assert c_suite > vp > director


def test_vice_president_is_not_misclassified_as_c_suite():
    """'Vice President' contains the substring 'president' - must not
    accidentally match the c-suite bucket instead of VP."""

    assert contact_seniority_score("Vice President of Engineering") == 0.8


def test_unknown_title_gets_a_modest_default_not_zero():
    assert contact_seniority_score("Something Unusual") == 0.3
    assert contact_seniority_score(None) == 0.3


# --- compute_priority_score: combining all three dimensions ----------------

def test_higher_tier_produces_higher_score_all_else_equal():
    contact = _contact("VP of Sales")
    tier_1 = compute_priority_score(FitTier.TIER_1, [], contact, now=NOW)
    tier_2 = compute_priority_score(FitTier.TIER_2, [], contact, now=NOW)
    assert tier_1 > tier_2


def test_score_is_always_in_zero_to_one_range():
    contact = _contact("Chief Executive Officer")
    evidence = [_evidence(ConfidenceLevel.HIGH, days_old=0)]
    score = compute_priority_score(FitTier.TIER_1, evidence, contact, now=NOW)
    assert 0.0 <= score <= 1.0


def test_invalid_weights_passed_to_compute_priority_score_are_rejected():
    with pytest.raises(ValueError):
        compute_priority_score(
            FitTier.TIER_1, [], _contact(), weights={"fit": 1.0, "evidence": 1.0, "contact_seniority": 1.0}
        )


# --- the "no dominant weight" guard, proven with a concrete case -----------

def test_evidence_alone_can_flip_the_ranking_of_two_otherwise_tied_prospects():
    """The roadmap's explicit test requirement: no factor should be able
    to drown out the others. Two prospects tied on fit and contact
    seniority, differing only on evidence recency, must rank differently -
    proving evidence isn't structurally inert under the default weights."""

    contact = _contact("VP of Sales")
    fresh_evidence_score = compute_priority_score(
        FitTier.TIER_2, [_evidence(ConfidenceLevel.HIGH, days_old=1)], contact, now=NOW
    )
    stale_evidence_score = compute_priority_score(
        FitTier.TIER_2, [_evidence(ConfidenceLevel.LOW, days_old=300)], contact, now=NOW
    )
    assert fresh_evidence_score > stale_evidence_score


def test_changing_weights_reorders_the_same_underlying_data():
    """The roadmap's stated exit criteria: re-runnable against a changed
    weighting without touching upstream data."""

    tier_2_with_strong_evidence = (
        FitTier.TIER_2,
        [_evidence(ConfidenceLevel.HIGH, days_old=0)],
        _contact("Manager"),
    )
    tier_1_with_no_evidence = (FitTier.TIER_1, [], _contact("Manager"))

    evidence_heavy_weights = {"fit": 0.2, "evidence": 0.7, "contact_seniority": 0.1}
    fit_heavy_weights = {"fit": 0.7, "evidence": 0.2, "contact_seniority": 0.1}

    score_a_evidence_heavy = compute_priority_score(*tier_2_with_strong_evidence, weights=evidence_heavy_weights, now=NOW)
    score_b_evidence_heavy = compute_priority_score(*tier_1_with_no_evidence, weights=evidence_heavy_weights, now=NOW)
    assert score_a_evidence_heavy > score_b_evidence_heavy  # evidence-heavy prospect wins

    score_a_fit_heavy = compute_priority_score(*tier_2_with_strong_evidence, weights=fit_heavy_weights, now=NOW)
    score_b_fit_heavy = compute_priority_score(*tier_1_with_no_evidence, weights=fit_heavy_weights, now=NOW)
    assert score_b_fit_heavy > score_a_fit_heavy  # fit-heavy prospect wins - ranking flipped
