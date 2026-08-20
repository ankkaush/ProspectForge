import uuid

from prospectforge.fit.rules import evaluate_criterion
from prospectforge.icp.models import Criterion, CriterionCategory, CriterionOperator, EnrichmentPhase
from prospectforge.models import Account


def _account(**overrides) -> Account:
    defaults = dict(id=uuid.uuid4(), domain="example.com", name="Example Co")
    defaults.update(overrides)
    return Account(**defaults)


def _criterion(field, operator, value) -> Criterion:
    return Criterion(
        field=field,
        operator=operator,
        value=value,
        phase=EnrichmentPhase.PRE_ENRICHMENT,
        category=CriterionCategory.FIRMOGRAPHIC,
        description="test criterion",
    )


def test_missing_field_evaluates_to_none_not_false():
    account = _account()  # industry unset
    criterion = _criterion("industry", CriterionOperator.IN, ["SaaS"])
    assert evaluate_criterion(account, criterion) is None


def test_in_operator_true_and_false():
    account = _account(industry="SaaS")
    assert evaluate_criterion(account, _criterion("industry", CriterionOperator.IN, ["SaaS"])) is True
    assert evaluate_criterion(account, _criterion("industry", CriterionOperator.IN, ["Retail"])) is False


def test_between_operator():
    account = _account(employee_count=200)
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.BETWEEN, [50, 500])) is True
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.BETWEEN, [500, 1000])) is False


def test_gte_and_lte_operators():
    account = _account(employee_count=100)
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.GTE, 50)) is True
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.GTE, 200)) is False
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.LTE, 200)) is True
    assert evaluate_criterion(account, _criterion("employee_count", CriterionOperator.LTE, 50)) is False


def test_equals_operator():
    account = _account(geography="Germany")
    assert evaluate_criterion(account, _criterion("geography", CriterionOperator.EQUALS, "Germany")) is True
    assert evaluate_criterion(account, _criterion("geography", CriterionOperator.EQUALS, "France")) is False


def test_contains_operator_on_list_field_is_any_overlap():
    account = _account(tech_stack=["Salesforce", "Zendesk"])
    criterion = _criterion("tech_stack", CriterionOperator.CONTAINS, ["Salesforce", "Slack"])
    assert evaluate_criterion(account, criterion) is True

    no_overlap = _criterion("tech_stack", CriterionOperator.CONTAINS, ["HubSpot"])
    assert evaluate_criterion(account, no_overlap) is False
