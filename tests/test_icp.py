import pytest

from prospectforge.icp import loader as loader_module
from prospectforge.icp.loader import ICPConfigError, load_icp_config
from prospectforge.icp.models import EnrichmentPhase


# --- the real seed config loads correctly -----------------------------

def test_seed_config_loads_successfully():
    config = load_icp_config("saas-fictional-v1")
    assert config.id == "saas-fictional-v1"
    assert config.version == 1


def test_seed_config_tier_1_is_stricter_than_tier_2():
    config = load_icp_config("saas-fictional-v1")
    assert len(config.tier_1_criteria) > len(config.tier_2_criteria)


def test_seed_config_has_disqualifiers_distinct_from_tier_criteria():
    config = load_icp_config("saas-fictional-v1")
    disqualifier_fields = {d.field for d in config.disqualifiers}
    tier_1_fields = {c.field for c in config.tier_1_criteria}
    # employee_count appears in both, but as a different kind of check
    # (a floor disqualifier vs. a preferred range) - not simply the
    # logical inverse of the tier_1 criterion, which is the point Step 2
    # made about disqualifiers.
    assert "industry" in disqualifier_fields
    assert disqualifier_fields != tier_1_fields


def test_seed_config_criteria_have_correct_phase_tagging():
    config = load_icp_config("saas-fictional-v1")
    by_field = {c.field: c.phase for c in config.tier_1_criteria}
    assert by_field["industry"] == EnrichmentPhase.PRE_ENRICHMENT
    assert by_field["employee_count"] == EnrichmentPhase.PRE_ENRICHMENT
    assert by_field["geography"] == EnrichmentPhase.PRE_ENRICHMENT
    assert by_field["tech_stack"] == EnrichmentPhase.POST_ENRICHMENT
    assert by_field["funding_stage"] == EnrichmentPhase.POST_ENRICHMENT


def test_unknown_config_id_raises():
    with pytest.raises(ICPConfigError, match="No ICP config found"):
        load_icp_config("does-not-exist")


# --- malformed / invalid configs are rejected at load time -------------

@pytest.fixture()
def broken_configs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_module, "CONFIGS_DIR", tmp_path)
    return tmp_path


def test_malformed_yaml_is_rejected(broken_configs_dir):
    (broken_configs_dir / "broken.yaml").write_text("id: [this is not: valid: yaml")
    with pytest.raises(ICPConfigError, match="Malformed YAML"):
        load_icp_config("broken")


def test_missing_required_field_is_rejected(broken_configs_dir):
    (broken_configs_dir / "incomplete.yaml").write_text(
        "id: incomplete\nversion: 1\n"  # missing `name`, `description`
    )
    with pytest.raises(ICPConfigError, match="Schema validation failed"):
        load_icp_config("incomplete")


def test_unknown_account_field_is_rejected(broken_configs_dir):
    (broken_configs_dir / "bad-field.yaml").write_text(
        """
id: bad-field
version: 1
name: Bad Field Config
description: has a typo'd field name
tier_1_criteria:
  - field: employe_count
    operator: gte
    value: 50
    phase: pre_enrichment
    category: firmographic
    description: "typo: should be employee_count"
"""
    )
    with pytest.raises(ICPConfigError, match="not a recognized Account field"):
        load_icp_config("bad-field")


def test_wrong_phase_for_pre_enrichment_field_is_rejected(broken_configs_dir):
    """The failure scenario the roadmap names explicitly: a criterion
    claiming a post-enrichment-only field is available pre-enrichment."""

    (broken_configs_dir / "wrong-phase.yaml").write_text(
        """
id: wrong-phase
version: 1
name: Wrong Phase Config
description: incorrectly claims tech_stack is available pre-enrichment
tier_1_criteria:
  - field: tech_stack
    operator: contains
    value: ["Salesforce"]
    phase: pre_enrichment
    category: technographic
    description: "WRONG: tech_stack is only known after enrichment"
"""
    )
    with pytest.raises(ICPConfigError, match="incorrect enrichment phase"):
        load_icp_config("wrong-phase")


def test_id_mismatch_between_filename_and_content_is_rejected(broken_configs_dir):
    (broken_configs_dir / "file-name.yaml").write_text(
        "id: different-internal-id\nversion: 1\nname: X\ndescription: X\n"
    )
    with pytest.raises(ICPConfigError, match="must match the filename"):
        load_icp_config("file-name")


def test_config_round_trips_through_model_dump(broken_configs_dir):
    (broken_configs_dir / "round-trip.yaml").write_text(
        """
id: round-trip
version: 2
name: Round Trip Config
description: used to check serialization round-trips cleanly
tier_1_criteria:
  - field: employee_count
    operator: gte
    value: 100
    phase: pre_enrichment
    category: firmographic
    description: "at least 100 employees"
"""
    )
    config = load_icp_config("round-trip")
    dumped = config.model_dump()
    restored = type(config).model_validate(dumped)
    assert restored == config
