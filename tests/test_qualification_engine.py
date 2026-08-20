import uuid
from datetime import datetime, timezone

from app.orm import ContactORM, EvidenceORM
from prospectforge.models.enums import ConfidenceLevel, EvidenceSourceType, FitTier, QualificationStatus
from prospectforge.qualification.engine import MAX_CONFIDENCE, TIER_BASE_CONFIDENCE, qualify_account


def _contact(email_confidence=None) -> ContactORM:
    return ContactORM(
        id=uuid.uuid4(), account_id=uuid.uuid4(), name="Jane Doe", title="VP of Sales",
        email_confidence=email_confidence, created_at=datetime.now(timezone.utc),
    )


def _evidence() -> EvidenceORM:
    return EvidenceORM(
        id=uuid.uuid4(), account_id=uuid.uuid4(), claim="Hiring engineers",
        source_type=EvidenceSourceType.AI_INFERRED, confidence=ConfidenceLevel.MEDIUM,
        extracted_at=datetime.now(timezone.utc),
    )


# --- no contact -> NOT_QUALIFIED, always -----------------------------------

def test_no_contact_is_not_qualified():
    account_id = uuid.uuid4()
    results = qualify_account(account_id, FitTier.TIER_1, ["good fit"], [], contacts=[])
    assert len(results) == 1
    assert results[0].status == QualificationStatus.NOT_QUALIFIED
    assert results[0].contact_id is None
    assert results[0].confidence == 0.0


# --- determinism: same inputs -> same outputs, every time ------------------

def test_qualification_is_deterministic():
    account_id = uuid.uuid4()
    contact = _contact(email_confidence="verified")
    evidence = [_evidence()]

    first = qualify_account(account_id, FitTier.TIER_1, ["reason"], evidence, [contact])
    second = qualify_account(account_id, FitTier.TIER_1, ["reason"], evidence, [contact])

    assert first[0].status == second[0].status
    assert first[0].confidence == second[0].confidence
    assert first[0].reasons == second[0].reasons


# --- tier + evidence + email confidence all feed the score -----------------

def test_tier_1_with_evidence_and_verified_email_scores_highest():
    account_id = uuid.uuid4()
    contact = _contact(email_confidence="verified")
    result = qualify_account(account_id, FitTier.TIER_1, ["r"], [_evidence()], [contact])[0]

    expected = min(TIER_BASE_CONFIDENCE[FitTier.TIER_1] + 0.10 + 0.05, MAX_CONFIDENCE)
    assert result.confidence == expected
    assert result.status == QualificationStatus.QUALIFIED


def test_tier_2_with_no_evidence_and_no_email_scores_lowest_of_the_qualified_cases():
    account_id = uuid.uuid4()
    contact = _contact(email_confidence=None)
    result = qualify_account(account_id, FitTier.TIER_2, ["r"], [], [contact])[0]

    assert result.confidence == TIER_BASE_CONFIDENCE[FitTier.TIER_2]
    assert result.status == QualificationStatus.QUALIFIED


def test_insufficient_data_fit_still_qualifies_but_at_low_confidence():
    """Missing enrichment data doesn't disqualify - it's reflected as a
    low confidence score, not a NOT_QUALIFIED verdict, consistent with
    Steps 8/10's 'missing data isn't rejection' principle."""

    account_id = uuid.uuid4()
    contact = _contact()
    result = qualify_account(account_id, FitTier.INSUFFICIENT_DATA, ["r"], [], [contact])[0]

    assert result.status == QualificationStatus.QUALIFIED
    assert result.confidence == TIER_BASE_CONFIDENCE[FitTier.INSUFFICIENT_DATA]
    assert result.confidence < TIER_BASE_CONFIDENCE[FitTier.TIER_2]
    assert any("could not be fully confirmed" in r for r in result.reasons)


def test_tier_3_or_rejected_is_not_qualified_defensively():
    """Shouldn't normally reach this engine (Step 11 routes these to
    REJECTED before research), but must be handled gracefully if it does."""

    account_id = uuid.uuid4()
    contact = _contact()
    for tier in (FitTier.TIER_3, FitTier.REJECTED):
        result = qualify_account(account_id, tier, ["r"], [], [contact])[0]
        assert result.status == QualificationStatus.NOT_QUALIFIED


def test_missing_fit_result_is_handled_defensively():
    account_id = uuid.uuid4()
    contact = _contact()
    result = qualify_account(account_id, None, [], [], [contact])[0]
    assert result.status == QualificationStatus.NOT_QUALIFIED


# --- multiple contacts get independently-scored results --------------------

def test_multiple_contacts_get_one_result_each_scored_independently():
    account_id = uuid.uuid4()
    verified_contact = _contact(email_confidence="verified")
    unverified_contact = _contact(email_confidence="unverified")

    results = qualify_account(
        account_id, FitTier.TIER_1, ["r"], [], [verified_contact, unverified_contact]
    )

    assert len(results) == 2
    by_contact = {r.contact_id: r for r in results}
    assert by_contact[verified_contact.id].confidence > by_contact[unverified_contact.id].confidence


# --- confidence never exceeds the cap ---------------------------------------

def test_confidence_never_exceeds_max_confidence():
    account_id = uuid.uuid4()
    contact = _contact(email_confidence="verified")
    result = qualify_account(account_id, FitTier.TIER_1, ["r"], [_evidence(), _evidence()], [contact])[0]
    assert result.confidence <= MAX_CONFIDENCE


# --- evidence_ids are carried through for the rationale step's use ---------

def test_evidence_ids_are_included_in_the_result():
    account_id = uuid.uuid4()
    contact = _contact()
    evidence = [_evidence(), _evidence()]
    result = qualify_account(account_id, FitTier.TIER_1, ["r"], evidence, [contact])[0]
    assert set(result.evidence_ids) == {e.id for e in evidence}
