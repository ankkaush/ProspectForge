import uuid

from prospectforge.fit.evaluator import evaluate_full_fit
from prospectforge.icp.loader import load_icp_config
from prospectforge.models import Account, FitPassType, FitTier

ICP = load_icp_config("saas-fictional-v1")


def _account(**overrides) -> Account:
    defaults = dict(id=uuid.uuid4(), domain="example.com", name="Example Co")
    defaults.update(overrides)
    return Account(**defaults)


def _full_fit_account(**overrides) -> Account:
    """A baseline account that satisfies every Tier 1 criterion in the
    seed ICP, so individual tests can override just the field(s) they
    want to probe."""

    defaults = dict(
        industry="Computer Software",
        employee_count=120,
        geography="United States",
        tech_stack=["Salesforce", "Zendesk"],
        funding_stage="Series B",
    )
    defaults.update(overrides)
    return _account(**defaults)


def test_full_match_is_tier_1():
    result = evaluate_full_fit(_full_fit_account(), ICP)
    assert result.tier == FitTier.TIER_1
    assert result.pass_type == FitPassType.FULL
    assert len(result.reasons) == 5  # all 5 tier_1 criteria


def test_missing_tech_stack_falls_back_to_tier_2_with_explanation():
    account = _full_fit_account(tech_stack=None)
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_2
    assert any("prevented Tier 1" in r and "tech_stack" in r for r in result.reasons)


def test_wrong_tech_stack_falls_back_to_tier_2_with_explanation():
    """A definite failure (has a tech stack, just not the right one) is a
    different reason than missing data, and should say so."""

    account = _full_fit_account(tech_stack=["HubSpot"])
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_2
    assert any("did not meet Tier 1" in r for r in result.reasons)


def test_missing_funding_stage_falls_back_to_tier_2():
    account = _full_fit_account(funding_stage=None)
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_2


def test_both_post_enrichment_fields_missing_still_lands_at_tier_2_not_insufficient_data():
    """This is the live scenario Step 9 actually produced: enrichment
    found nothing at all. The firmographics are still fully confirmed, so
    this should be a confident Tier 2, not a vague 'insufficient data' -
    we know enough to say it's a real fit, just not enough for Tier 1."""

    account = _full_fit_account(tech_stack=None, funding_stage=None)
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_2


def test_regulatory_disqualifier_rejects_even_with_perfect_tier_1_data():
    account = _full_fit_account(industry="Gambling & Casinos")
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.REJECTED


def test_definite_tier_2_failure_is_tier_3_not_rejected():
    """Wrong industry (not a disqualifier, just outside the tier list) -
    should land at Tier 3, a meaningfully different outcome from REJECTED
    (which is reserved for actual disqualifiers - see icp/models.py)."""

    account = _full_fit_account(industry="Retail")
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_3


def test_missing_firmographic_data_with_no_definite_failure_is_insufficient_data():
    account = _full_fit_account(geography=None)
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.INSUFFICIENT_DATA


def test_definite_failure_takes_precedence_over_missing_data_at_tier_2_too():
    account = _full_fit_account(industry="Retail", geography=None)
    result = evaluate_full_fit(account, ICP)
    assert result.tier == FitTier.TIER_3  # the definite failure wins, not "insufficient data"
