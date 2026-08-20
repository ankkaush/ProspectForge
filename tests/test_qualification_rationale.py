import uuid

from prospectforge.qualification.rationale import (
    build_rationale_text,
    parse_statements,
    templated_rationale,
)


# --- parse_statements: pure JSON parsing -----------------------------------

def test_parses_well_formed_json():
    raw = '{"statements": [{"text": "Good fit.", "based_on": "fit"}]}'
    assert parse_statements(raw) == [{"text": "Good fit.", "based_on": "fit"}]


def test_strips_markdown_code_fence():
    raw = '```json\n{"statements": []}\n```'
    assert parse_statements(raw) == []


def test_malformed_json_returns_none():
    assert parse_statements("not json at all") is None


def test_missing_statements_key_returns_none():
    assert parse_statements('{"other": []}') is None


def test_statements_not_a_list_returns_none():
    assert parse_statements('{"statements": "nope"}') is None


# --- build_rationale_text: validation and the evidence-id cross-check ------

def test_fit_and_contact_statements_are_always_kept():
    statements = [
        {"text": "Strong Tier 1 fit.", "based_on": "fit"},
        {"text": "Jane Doe is VP of Sales.", "based_on": "contact"},
    ]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[])
    assert dropped == 0
    assert "Strong Tier 1 fit." in text
    assert "Jane Doe is VP of Sales." in text


def test_evidence_statement_citing_a_real_id_is_kept():
    evidence_id = uuid.uuid4()
    statements = [{"text": "Posted new job listings.", "based_on": f"evidence:{evidence_id}"}]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[evidence_id])
    assert dropped == 0
    assert text == "Posted new job listings."


def test_evidence_statement_citing_a_fabricated_id_is_dropped():
    """The anti-hallucination check: a statement citing an evidence id
    that isn't actually in this account's evidence set is dropped, not
    trusted - the same structural pattern as Step 11's URL cross-check."""

    real_id = uuid.uuid4()
    fabricated_id = uuid.uuid4()
    statements = [{"text": "Made up fact.", "based_on": f"evidence:{fabricated_id}"}]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[real_id])
    assert text is None
    assert dropped == 1


def test_unrecognized_based_on_value_is_dropped():
    statements = [{"text": "X", "based_on": "made_up_category"}]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[])
    assert text is None
    assert dropped == 1


def test_statement_missing_required_fields_is_dropped():
    statements = [{"text": "X"}, {"based_on": "fit"}, {}]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[])
    assert text is None
    assert dropped == 3


def test_mixed_valid_and_invalid_statements_keeps_only_valid_ones():
    real_id = uuid.uuid4()
    statements = [
        {"text": "Valid fit statement.", "based_on": "fit"},
        {"text": "Fabricated evidence.", "based_on": f"evidence:{uuid.uuid4()}"},
        {"text": "Valid evidence statement.", "based_on": f"evidence:{real_id}"},
    ]
    text, dropped = build_rationale_text(statements, valid_evidence_ids=[real_id])
    assert dropped == 1
    assert "Valid fit statement." in text
    assert "Valid evidence statement." in text
    assert "Fabricated evidence." not in text


def test_empty_statements_list_returns_none():
    text, dropped = build_rationale_text([], valid_evidence_ids=[])
    assert text is None
    assert dropped == 0


# --- templated fallback -----------------------------------------------------

def test_templated_rationale_is_deterministic_and_always_available():
    reasons = ["Fit tier: tier_1", "Candidate decision-maker: Jane Doe"]
    text = templated_rationale(reasons)
    assert text == "Fit tier: tier_1; Candidate decision-maker: Jane Doe."
