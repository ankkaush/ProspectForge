import uuid

import pytest

from app.orm import AccountORM, ContactORM, FitResultORM, QualificationResultORM, RunORM
from infra.retry import NonRetryableError
from prospectforge.models.enums import (
    AccountStatus,
    FitPassType,
    FitTier,
    QualificationStatus,
    RunStatus,
)
from prospectforge.qualification.interface import RationaleContext, RationaleProvider, RationaleResult
from prospectforge.qualification.rationale import templated_rationale
from prospectforge.qualification.service import (
    resolve_rationale_provider,
    run_qualification,
)

# Captured at collection time, before conftest's autouse
# _fake_rationale_provider_by_default fixture monkeypatches
# service.get_default_rationale_provider for every test - this reference
# always points at the real function, letting a handful of tests below
# exercise the actual default-resolution behavior instead of the fake.
from prospectforge.qualification.service import (
    get_default_rationale_provider as real_get_default_rationale_provider,
)


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _researched_account(db_session, fit_tier=FitTier.TIER_1, with_contact=True) -> AccountORM:
    account = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co",
        status=AccountStatus.RESEARCHED,
    )
    db_session.add(account)
    db_session.flush()

    db_session.add(
        FitResultORM(
            account_id=account.id, pass_type=FitPassType.FULL, tier=fit_tier,
            reasons=["some reason"],
        )
    )
    if with_contact:
        db_session.add(
            ContactORM(id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales")
        )
    db_session.flush()
    return account


class _FixedRationaleProvider(RationaleProvider):
    def __init__(self, result=None, raises=None):
        self._result = result or RationaleResult(rationale_text="Generated rationale.")
        self._raises = raises

    def generate_rationale(self, context: RationaleContext) -> RationaleResult:
        if self._raises:
            raise self._raises
        return self._result


def test_qualified_account_transitions_and_persists_a_result(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1)

    summary = run_qualification(run.id, db_session, provider=_FixedRationaleProvider())

    assert summary["accounts_qualified"] == 1
    db_session.refresh(account)
    assert account.status == AccountStatus.QUALIFIED

    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert result.status == QualificationStatus.QUALIFIED
    assert result.rationale_text == "Generated rationale."


def test_account_with_no_contact_is_not_qualified(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1, with_contact=False)

    summary = run_qualification(run.id, db_session, provider=_FixedRationaleProvider())

    assert summary["accounts_not_qualified"] == 1
    db_session.refresh(account)
    assert account.status == AccountStatus.NOT_QUALIFIED

    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert result.status == QualificationStatus.NOT_QUALIFIED
    assert result.contact_id is None


def test_rationale_failure_falls_back_to_templated_text_without_failing_qualification(db_session):
    """The core guarantee: a rationale-generation failure changes only
    which sentence ends up in rationale_text, never the qualification
    verdict itself."""

    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1)
    failing_provider = _FixedRationaleProvider(raises=NonRetryableError("401"))

    summary = run_qualification(run.id, db_session, provider=failing_provider)

    assert summary["rationale_fallback"] == 1
    assert summary["accounts_qualified"] == 1  # verdict unaffected

    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert result.status == QualificationStatus.QUALIFIED  # still qualified
    assert "Fit tier" in result.rationale_text  # templated fallback, not the AI text


def test_multiple_contacts_produce_multiple_qualification_results(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1, with_contact=True)
    db_session.add(
        ContactORM(id=uuid.uuid4(), account_id=account.id, name="Carlos Mendez", title="Director of Sales")
    )
    db_session.flush()

    run_qualification(run.id, db_session, provider=_FixedRationaleProvider())

    results = db_session.query(QualificationResultORM).filter_by(account_id=account.id).all()
    assert len(results) == 2
    assert {r.contact_id for r in results} == {
        c.id for c in db_session.query(ContactORM).filter_by(account_id=account.id).all()
    }


def test_only_processes_researched_accounts(db_session):
    run = _bare_run(db_session)
    raw_account = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Raw Co",
        status=AccountStatus.RAW,
    )
    db_session.add(raw_account)
    db_session.flush()

    summary = run_qualification(run.id, db_session, provider=_FixedRationaleProvider())

    assert summary["accounts_evaluated"] == 0
    db_session.refresh(raw_account)
    assert raw_account.status == AccountStatus.RAW


def test_insufficient_data_fit_still_qualifies_with_a_contact(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.INSUFFICIENT_DATA)

    run_qualification(run.id, db_session, provider=_FixedRationaleProvider())

    db_session.refresh(account)
    assert account.status == AccountStatus.QUALIFIED
    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert result.confidence < 0.5  # low confidence, but still qualified


# --- default rationale provider: deterministic, no Anthropic call ---------

def test_resolve_rationale_provider_deterministic_setting_returns_none():
    assert resolve_rationale_provider("deterministic", anthropic_api_key="") is None
    assert resolve_rationale_provider("deterministic", anthropic_api_key="sk-ant-real-key") is None


def test_resolve_rationale_provider_anthropic_setting_without_key_raises():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        resolve_rationale_provider("anthropic", anthropic_api_key="")


def test_resolve_rationale_provider_anthropic_setting_with_key_returns_the_ai_provider():
    """Not the default, but still fully wired - the optional AI path was
    preserved, not removed."""

    from prospectforge.qualification.providers.anthropic_rationale import AnthropicRationaleProvider

    resolved = resolve_rationale_provider("anthropic", anthropic_api_key="sk-ant-fake-key-for-construction")
    assert isinstance(resolved, AnthropicRationaleProvider)


def test_resolve_rationale_provider_unknown_setting_raises():
    with pytest.raises(ValueError, match="Unknown qualification_rationale_provider"):
        resolve_rationale_provider("something-else", anthropic_api_key="")


def test_real_default_provider_is_deterministic_out_of_the_box():
    """The actual, unpatched get_default_rationale_provider() - proving the
    standard pipeline's real default is 'deterministic', not the fake
    every other test in this file gets from conftest.py's autouse
    _fake_rationale_provider_by_default fixture."""

    assert real_get_default_rationale_provider() is None


def test_default_pipeline_qualifies_and_generates_deterministic_rationale_without_anthropic(
    db_session, monkeypatch
):
    """Bypasses this file's autouse fake-provider fixture for one test, to
    prove run_qualification()'s real default path - no provider passed, no
    AI call made - still produces a complete, correct rationale_text, and
    the qualification verdict is identical to the AI-phrased path above."""

    import prospectforge.qualification.service as qualification_service

    monkeypatch.setattr(
        qualification_service, "get_default_rationale_provider", real_get_default_rationale_provider
    )

    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1)

    summary = run_qualification(run.id, db_session)  # no provider= argument at all

    assert summary["rationale_deterministic"] == 1
    assert summary["rationale_generated"] == 0
    assert summary["rationale_fallback"] == 0
    assert summary["accounts_qualified"] == 1

    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert result.status == QualificationStatus.QUALIFIED
    assert result.rationale_text == templated_rationale(result.reasons)


def test_anthropic_failure_still_cannot_change_the_verdict_or_confidence(db_session):
    """Re-confirms the pre-existing guarantee still holds with the new
    branch in place: even when the optional AI provider is explicitly
    selected and fails, only rationale_text is affected."""

    failing_provider = _FixedRationaleProvider(raises=NonRetryableError("simulated failure"))
    run = _bare_run(db_session)
    account = _researched_account(db_session, fit_tier=FitTier.TIER_1)

    summary = run_qualification(run.id, db_session, provider=failing_provider)

    result = db_session.query(QualificationResultORM).filter_by(account_id=account.id).one()
    assert summary["rationale_fallback"] == 1
    assert result.status == QualificationStatus.QUALIFIED
    assert result.confidence > 0
    assert result.rationale_text == templated_rationale(result.reasons)
