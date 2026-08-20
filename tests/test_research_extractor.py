import uuid

from prospectforge.research.extractor import build_evidence, parse_claims


# --- parse_claims: pure JSON parsing, no API calls needed -----------------

def test_parses_well_formed_json():
    raw = '{"claims": [{"claim": "Hired a VP of Sales", "source_url": "https://x.com/news", "confidence": "high"}]}'
    claims = parse_claims(raw)
    assert claims == [{"claim": "Hired a VP of Sales", "source_url": "https://x.com/news", "confidence": "high"}]


def test_strips_markdown_code_fence():
    raw = '```json\n{"claims": []}\n```'
    assert parse_claims(raw) == []


def test_strips_bare_code_fence_without_json_language_tag():
    raw = '```\n{"claims": []}\n```'
    assert parse_claims(raw) == []


def test_malformed_json_returns_none_not_an_exception():
    assert parse_claims("this is not json at all {{{") is None


def test_valid_json_without_claims_key_returns_none():
    assert parse_claims('{"result": "no claims field here"}') is None


def test_claims_that_is_not_a_list_returns_none():
    assert parse_claims('{"claims": "not a list"}') is None


def test_empty_claims_list_is_valid():
    assert parse_claims('{"claims": []}') == []


# --- build_evidence: validation and the source-verification cross-check ---

def test_valid_claim_with_verified_source_becomes_evidence():
    account_id = uuid.uuid4()
    raw_claims = [
        {"claim": "Raised a Series B", "source_url": "https://techcrunch.com/x", "confidence": "high"}
    ]
    evidence, dropped = build_evidence(account_id, raw_claims, {"https://techcrunch.com/x"})

    assert dropped == 0
    assert len(evidence) == 1
    assert evidence[0].account_id == account_id
    assert evidence[0].claim == "Raised a Series B"
    assert evidence[0].confidence.value == "high"


def test_claim_citing_unverified_url_is_dropped():
    """The anti-hallucination check: a claim citing a URL the model never
    actually retrieved via web_search this turn is dropped, not trusted."""

    raw_claims = [
        {"claim": "Fabricated fact", "source_url": "https://never-searched.com", "confidence": "high"}
    ]
    evidence, dropped = build_evidence(uuid.uuid4(), raw_claims, {"https://real-source.com"})

    assert evidence == []
    assert dropped == 1


def test_claim_missing_a_required_field_is_dropped():
    raw_claims = [{"claim": "Something happened", "confidence": "medium"}]  # no source_url
    evidence, dropped = build_evidence(uuid.uuid4(), raw_claims, set())

    assert evidence == []
    assert dropped == 1


def test_claim_with_invalid_confidence_value_is_dropped():
    raw_claims = [
        {"claim": "X happened", "source_url": "https://x.com", "confidence": "extremely-sure"}
    ]
    evidence, dropped = build_evidence(uuid.uuid4(), raw_claims, {"https://x.com"})

    assert evidence == []
    assert dropped == 1


def test_mixed_valid_and_invalid_claims_keeps_only_the_valid_ones():
    account_id = uuid.uuid4()
    raw_claims = [
        {"claim": "Valid claim", "source_url": "https://real.com", "confidence": "medium"},
        {"claim": "Bad source", "source_url": "https://fake.com", "confidence": "high"},
        {"claim": "No confidence field", "source_url": "https://real.com"},
    ]
    evidence, dropped = build_evidence(account_id, raw_claims, {"https://real.com"})

    assert len(evidence) == 1
    assert evidence[0].claim == "Valid claim"
    assert dropped == 2


def test_non_dict_claim_entries_are_dropped_not_crashed_on():
    evidence, dropped = build_evidence(uuid.uuid4(), ["not a dict", 42, None], {"https://x.com"})
    assert evidence == []
    assert dropped == 3
