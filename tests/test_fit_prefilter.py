import uuid

from prospectforge.fit.prefilter import prefilter_account
from prospectforge.icp.loader import load_icp_config
from prospectforge.models import Account, FitPassType, FitTier

ICP = load_icp_config("saas-fictional-v1")


def _account(**overrides) -> Account:
    defaults = dict(id=uuid.uuid4(), domain="example.com", name="Example Co")
    defaults.update(overrides)
    return Account(**defaults)


def test_full_match_advances_as_tier_2():
    account = _account(industry="Computer Software", employee_count=120, geography="United States")
    result = prefilter_account(account, ICP)
    assert result.pass_type == FitPassType.PREFILTER
    assert result.tier == FitTier.TIER_2
    assert len(result.reasons) == 3  # one per pre-enrichment tier_2 criterion


def test_regulatory_disqualifier_rejects_regardless_of_other_fields():
    account = _account(industry="Government Administration", employee_count=200, geography="United States")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED
    assert "Regulatory" in result.reasons[0]


def test_too_small_disqualifier_rejects():
    account = _account(industry="Computer Software", employee_count=5, geography="United States")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED
    assert "too small" in result.reasons[0].lower() or "categorically" in result.reasons[0].lower()


def test_wrong_industry_is_rejected_not_disqualified():
    """Outside the tier's industry list but not a disqualifier - a
    different rejection path (tier_2 criterion failure) with a different
    reason than the regulatory disqualifier."""

    account = _account(industry="Retail", employee_count=120, geography="United States")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED
    assert "Regulatory" not in result.reasons[0]


def test_employee_count_outside_range_but_above_disqualifier_floor_is_rejected():
    account = _account(industry="Computer Software", employee_count=800, geography="United States")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED


def test_unsupported_geography_is_rejected():
    account = _account(industry="Computer Software", employee_count=120, geography="Brazil")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED


def test_missing_field_advances_as_insufficient_data():
    account = _account(industry="Computer Software", employee_count=120)  # geography unset
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.INSUFFICIENT_DATA
    assert any("geography" in r for r in result.reasons)


def test_definite_failure_takes_precedence_over_missing_data():
    """Wrong industry (definite failure) plus missing geography (unknown) -
    the definite failure should win; this shouldn't come back as merely
    'insufficient data' when part of it is already a clear no."""

    account = _account(industry="Retail", employee_count=120)  # geography unset
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.REJECTED


def test_employee_count_at_the_tier_floor_passes_and_is_not_disqualified():
    """50 sits exactly at the tier's lower bound and well above the
    disqualifier's floor of 9 - confirms the two employee_count checks
    (disqualifier vs. tier range) don't interfere with each other at a
    boundary value."""

    account = _account(employee_count=50, industry="Computer Software", geography="United States")
    result = prefilter_account(account, ICP)
    assert result.tier == FitTier.TIER_2
