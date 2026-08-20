import uuid

import pytest
from pydantic import ValidationError

from prospectforge.models import (
    Account,
    AccountStatus,
    CallStatus,
    ConfidenceLevel,
    Contact,
    Evidence,
    EvidenceSourceType,
    ExternalCallAttempt,
    FitPassType,
    FitResult,
    FitTier,
    IllegalStatusTransition,
    ProspectRecord,
    ProviderRecord,
    QualificationResult,
    QualificationStatus,
    ReviewDecision,
    Run,
    RunStatus,
)


# --- Account: construction, unknown-vs-empty, invalid data ---------------

def test_account_constructs_with_only_required_fields():
    account = Account(domain="northstar-metrics.com", name="Northstar Metrics")
    assert account.status == AccountStatus.RAW
    assert account.employee_count is None  # unknown, not zero
    assert account.tech_stack is None  # unknown, not an empty list


def test_account_rejects_invalid_employee_count_type():
    with pytest.raises(ValidationError):
        Account(domain="x.com", name="X", employee_count="a lot")  # not an int


def test_account_round_trips_through_json():
    account = Account(domain="x.com", name="X", employee_count=120)
    restored = Account.model_validate_json(account.model_dump_json())
    assert restored == account


# --- Account status state machine -----------------------------------------

def test_legal_status_transition_succeeds():
    account = Account(domain="x.com", name="X")
    account.transition_to(AccountStatus.ADVANCED)
    assert account.status == AccountStatus.ADVANCED


def test_illegal_status_transition_is_rejected():
    account = Account(domain="x.com", name="X")  # status = RAW
    with pytest.raises(IllegalStatusTransition):
        account.transition_to(AccountStatus.SYNCED)  # skips every gate
    assert account.status == AccountStatus.RAW  # unchanged on rejection


def test_terminal_status_has_no_legal_next_state():
    account = Account(domain="x.com", name="X")
    account.transition_to(AccountStatus.REJECTED_EARLY)
    with pytest.raises(IllegalStatusTransition):
        account.transition_to(AccountStatus.ADVANCED)


def test_enrichment_failed_can_be_retried_to_enriched():
    account = Account(domain="x.com", name="X")
    account.transition_to(AccountStatus.ADVANCED)
    account.transition_to(AccountStatus.ENRICHMENT_FAILED)
    account.transition_to(AccountStatus.ENRICHED)  # retry succeeds
    assert account.status == AccountStatus.ENRICHED


# --- Contact: unverified email is never silently "verified" ---------------

def test_contact_email_confidence_defaults_to_unknown_not_verified():
    contact = Contact(account_id=uuid.uuid4(), name="Jane Doe")
    assert contact.email_confidence is None


# --- Evidence: provenance is required, confidence is required -------------

def test_evidence_requires_source_type_and_confidence():
    with pytest.raises(ValidationError):
        Evidence(account_id=uuid.uuid4(), claim="hiring engineers")  # missing both


def test_evidence_constructs_with_provenance():
    ev = Evidence(
        account_id=uuid.uuid4(),
        claim="Posted 4 open engineering roles in the last 30 days",
        source_type=EvidenceSourceType.AI_INFERRED,
        source_url="https://northstar-metrics.com/careers",
        confidence=ConfidenceLevel.MEDIUM,
    )
    assert ev.confidence == ConfidenceLevel.MEDIUM


# --- QualificationResult: confidence is bounded, rationale traces to evidence

def test_qualification_confidence_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        QualificationResult(
            account_id=uuid.uuid4(),
            status=QualificationStatus.QUALIFIED,
            confidence=1.5,
        )


def test_qualification_result_partial_data_is_needs_more_info_not_a_guess():
    result = QualificationResult(
        account_id=uuid.uuid4(),
        status=QualificationStatus.NEEDS_MORE_INFO,
        confidence=0.2,
        reasons=["no verified contact found"],
    )
    assert result.status == QualificationStatus.NEEDS_MORE_INFO


# --- Run and ExternalCallAttempt: the audit's resumability primitives -----

def test_run_starts_pending_with_empty_summary():
    run = Run(icp_config_id="saas-fictional-v1")
    assert run.status == RunStatus.PENDING
    assert run.summary == {}


def test_external_call_attempt_records_a_failed_retry():
    run = Run(icp_config_id="saas-fictional-v1")
    attempt = ExternalCallAttempt(
        run_id=run.id,
        provider="apollo",
        operation="account_enrichment",
        attempt_number=2,
        status=CallStatus.FAILED_RETRYABLE,
        error_message="timeout after 10s",
    )
    assert attempt.attempt_number == 2
    assert attempt.status == CallStatus.FAILED_RETRYABLE


def test_external_call_attempt_number_must_be_at_least_one():
    with pytest.raises(ValidationError):
        ExternalCallAttempt(
            run_id=uuid.uuid4(),
            provider="apollo",
            operation="discovery",
            attempt_number=0,
            status=CallStatus.SUCCESS,
        )


# --- ProviderRecord and FitResult: structural sanity checks ---------------

def test_provider_record_keeps_provider_as_plain_string():
    record = ProviderRecord(
        provider="apollo", operation="discovery", payload={"id": "abc"}
    )
    assert record.provider == "apollo"


def test_fit_result_records_pass_type_and_reasons():
    result = FitResult(
        account_id=uuid.uuid4(),
        pass_type=FitPassType.PREFILTER,
        tier=FitTier.TIER_1,
        reasons=["industry matches", "employee count in range"],
    )
    assert result.pass_type == FitPassType.PREFILTER
    assert len(result.reasons) == 2


# --- ProspectRecord: defaults to pending review, not auto-approved --------

def test_prospect_record_defaults_to_pending_review():
    prospect = ProspectRecord(
        account_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        qualification_result_id=uuid.uuid4(),
    )
    assert prospect.review_decision == ReviewDecision.PENDING
    assert prospect.crm_object_id is None
