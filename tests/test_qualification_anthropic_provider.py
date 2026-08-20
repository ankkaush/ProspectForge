from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.qualification.interface import EvidenceSummary, RationaleContext
from prospectforge.qualification.providers.anthropic_rationale import AnthropicRationaleProvider


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _response(text: str):
    return SimpleNamespace(content=[_text_block(text)])


def _provider_with_client(mock_create) -> AnthropicRationaleProvider:
    client = MagicMock()
    client.messages.create = mock_create
    return AnthropicRationaleProvider(api_key="test-key", client=client)


def _context(evidence_id=None) -> RationaleContext:
    return RationaleContext(
        account_name="Northstar Metrics",
        fit_tier="tier_1",
        deterministic_reasons=["Fit tier: tier_1", "Candidate decision-maker: Jane Doe"],
        evidence=[EvidenceSummary(id=evidence_id, claim="Hiring engineers", confidence="medium")]
        if evidence_id
        else [],
        contact_name="Jane Doe",
        contact_title="VP of Sales",
        contact_email_confidence="verified",
    )


def test_maps_a_successful_response():
    import uuid

    evidence_id = uuid.uuid4()
    response = _response(
        f'{{"statements": [{{"text": "Strong Tier 1 fit.", "based_on": "fit"}}, '
        f'{{"text": "Hiring engineers.", "based_on": "evidence:{evidence_id}"}}]}}'
    )
    provider = _provider_with_client(MagicMock(return_value=response))

    result = provider.generate_rationale(_context(evidence_id))

    assert result.rationale_text == "Strong Tier 1 fit. Hiring engineers."
    assert result.dropped_statement_count == 0


def test_fabricated_evidence_id_is_dropped():
    response = _response(
        '{"statements": [{"text": "Made up.", "based_on": "evidence:00000000-0000-0000-0000-000000000000"}]}'
    )
    provider = _provider_with_client(MagicMock(return_value=response))

    result = provider.generate_rationale(_context())  # no real evidence in context

    assert result.rationale_text is None
    assert result.dropped_statement_count == 1


def test_retries_once_on_malformed_json_then_succeeds():
    bad = _response("not json")
    good = _response('{"statements": [{"text": "Strong fit.", "based_on": "fit"}]}')
    mock_create = MagicMock(side_effect=[bad, good])
    provider = _provider_with_client(mock_create)

    result = provider.generate_rationale(_context())

    assert mock_create.call_count == 2
    assert result.rationale_text == "Strong fit."


def test_falls_back_to_none_after_exhausting_json_retries():
    always_bad = _response("still not json")
    mock_create = MagicMock(return_value=always_bad)
    provider = _provider_with_client(mock_create)

    result = provider.generate_rationale(_context())

    assert result.rationale_text is None
    assert mock_create.call_count == 2


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_rate_limit_is_retryable():
    response = httpx.Response(429, request=_fake_request())
    mock_create = MagicMock(side_effect=anthropic.RateLimitError("rate limited", response=response, body=None))
    provider = _provider_with_client(mock_create)

    with pytest.raises(RetryableError):
        provider.generate_rationale(_context())


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    response = httpx.Response(status_code, request=_fake_request())
    mock_create = MagicMock(
        side_effect=anthropic.APIStatusError("bad request", response=response, body=None)
    )
    provider = _provider_with_client(mock_create)

    with pytest.raises(NonRetryableError):
        provider.generate_rationale(_context())


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        AnthropicRationaleProvider(api_key="")
