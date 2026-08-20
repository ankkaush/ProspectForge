import pytest

from prospectforge.persona.loader import PersonaConfigError, load_persona_config
from prospectforge.persona.matcher import match_title, match_title_against_keywords
from prospectforge.persona.models import PersonaConfig


# --- loader ------------------------------------------------------------

def test_seed_persona_config_loads_successfully():
    persona = load_persona_config("primary-buyer-v1")
    assert persona.id == "primary-buyer-v1"
    assert "VP" in persona.seniority_keywords


def test_unknown_persona_id_raises():
    with pytest.raises(PersonaConfigError, match="No persona config found"):
        load_persona_config("does-not-exist")


def test_empty_seniority_keywords_rejected():
    with pytest.raises(Exception):
        PersonaConfig(
            id="x",
            version=1,
            name="X",
            description="X",
            seniority_keywords=[],
            department_keywords=["Sales"],
        )


def test_empty_department_keywords_rejected():
    with pytest.raises(Exception):
        PersonaConfig(
            id="x",
            version=1,
            name="X",
            description="X",
            seniority_keywords=["VP"],
            department_keywords=[],
        )


# --- matcher -------------------------------------------------------------

def test_matches_title_with_both_seniority_and_department_keywords():
    persona = load_persona_config("primary-buyer-v1")
    result = match_title("VP of Revenue Operations", persona)
    assert result is not None
    assert "VP" in result
    assert "Revenue" in result


def test_seniority_alone_does_not_match():
    persona = load_persona_config("primary-buyer-v1")
    assert match_title("VP of Marketing", persona) is None  # no department keyword hit


def test_department_alone_does_not_match():
    persona = load_persona_config("primary-buyer-v1")
    assert match_title("Sales Operations Coordinator", persona) is None  # no seniority keyword


def test_no_title_does_not_match():
    persona = load_persona_config("primary-buyer-v1")
    assert match_title(None, persona) is None
    assert match_title("", persona) is None


def test_matching_is_case_insensitive():
    result = match_title_against_keywords("vp of engineering", ["VP"], ["Engineering"])
    assert result is not None


def test_match_title_against_keywords_is_the_shared_primitive():
    """Both persona.matcher's PersonaConfig-based match_title and the
    provider-independent PersonSearchCriteria path (CSV/Apollo providers)
    go through this same function - verified directly here."""

    assert match_title_against_keywords("Director of IT", ["Director"], ["IT"]) is not None
    assert match_title_against_keywords("Director of IT", ["VP"], ["IT"]) is None
