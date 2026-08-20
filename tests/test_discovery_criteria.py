from prospectforge.discovery.criteria import criteria_from_icp
from prospectforge.icp.loader import load_icp_config


def test_criteria_from_real_seed_icp_pulls_pre_enrichment_fields_only():
    icp = load_icp_config("saas-fictional-v1")
    criteria = criteria_from_icp(icp)

    assert criteria.industries == ["Computer Software", "Internet Software & Services", "SaaS"]
    assert criteria.geographies == [
        "United States",
        "Canada",
        "United Kingdom",
        "Germany",
        "Netherlands",
        "Ireland",
        "France",
    ]
    assert criteria.employee_count_min == 50
    assert criteria.employee_count_max == 500


def test_criteria_never_includes_post_enrichment_fields():
    """tech_stack and funding_stage are post-enrichment criteria in the
    seed config - a discovery query built from them would be asking Apollo
    to filter on data we don't have yet, which is exactly what Step 6's
    phase tagging exists to prevent."""

    icp = load_icp_config("saas-fictional-v1")
    criteria = criteria_from_icp(icp)

    assert not hasattr(criteria, "tech_stack")
    assert not hasattr(criteria, "funding_stage")
