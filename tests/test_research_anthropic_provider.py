"""Tests for AnthropicWebSearchResearchProvider - mocks the Anthropic SDK
client directly (no real API key or network access needed), matching the
same discipline as the Apollo provider tests: verify the mapping and retry
logic against realistic response shapes grounded in Anthropic's actual API
docs.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account
from prospectforge.research.providers.anthropic_web_search import (
    AnthropicWebSearchResearchProvider,
)


def _account() -> Account:
    return Account(domain="northstar-metrics.com", name="Northstar Metrics")


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _search_result_block(urls: list):
    return SimpleNamespace(
        type="web_search_tool_result",
        content=[SimpleNamespace(url=url, title="x") for url in urls],
    )


def _response(blocks: list):
    return SimpleNamespace(content=blocks)


def _provider_with_client(mock_create) -> AnthropicWebSearchResearchProvider:
    client = MagicMock()
    client.messages.create = mock_create
    return AnthropicWebSearchResearchProvider(api_key="test-key", client=client)


# --- successful mapping ------------------------------------------------

def test_maps_a_successful_response_with_verified_source():
    response = _response(
        [
            _text_block("I'll search for recent news."),
            _search_result_block(["https://techcrunch.com/northstar"]),
            _text_block(
                '{"claims": [{"claim": "Raised a Series B round", '
                '"source_url": "https://techcrunch.com/northstar", "confidence": "high"}]}'
            ),
        ]
    )
    provider = _provider_with_client(MagicMock(return_value=response))

    result = provider.research_account(_account())

    assert len(result.evidence) == 1
    assert result.evidence[0].claim == "Raised a Series B round"
    assert result.evidence[0].source_url == "https://techcrunch.com/northstar"
    assert result.dropped_claim_count == 0
    assert result.verified_urls == ["https://techcrunch.com/northstar"]


def test_claim_citing_a_url_not_actually_searched_is_dropped():
    response = _response(
        [
            _search_result_block(["https://real-source.com"]),
            _text_block(
                '{"claims": [{"claim": "Made up fact", '
                '"source_url": "https://never-searched.com", "confidence": "high"}]}'
            ),
        ]
    )
    provider = _provider_with_client(MagicMock(return_value=response))

    result = provider.research_account(_account())

    assert result.evidence == []
    assert result.dropped_claim_count == 1


def test_empty_claims_is_a_valid_successful_result():
    response = _response(
        [_search_result_block(["https://x.com"]), _text_block('{"claims": []}')]
    )
    provider = _provider_with_client(MagicMock(return_value=response))

    result = provider.research_account(_account())
    assert result.evidence == []
    assert result.dropped_claim_count == 0


# --- malformed JSON: retry once, then fall back -------------------------

def test_retries_once_on_malformed_json_then_succeeds():
    bad_response = _response([_text_block("Sorry, here's my answer: not json")])
    good_response = _response(
        [
            _search_result_block(["https://x.com"]),
            _text_block('{"claims": [{"claim": "Fact", "source_url": "https://x.com", "confidence": "low"}]}'),
        ]
    )
    mock_create = MagicMock(side_effect=[bad_response, good_response])
    provider = _provider_with_client(mock_create)

    result = provider.research_account(_account())

    assert mock_create.call_count == 2
    assert len(result.evidence) == 1


def test_falls_back_to_no_evidence_after_exhausting_json_retries():
    always_bad = _response([_text_block("still not json")])
    mock_create = MagicMock(return_value=always_bad)
    provider = _provider_with_client(mock_create)

    result = provider.research_account(_account())

    assert result.evidence == []
    assert mock_create.call_count == 2  # 1 initial + 1 retry (MAX_JSON_RETRIES=1)


def test_response_with_no_text_block_at_all_falls_back_gracefully():
    response = _response([_search_result_block(["https://x.com"])])  # no text block
    mock_create = MagicMock(return_value=response)
    provider = _provider_with_client(mock_create)

    result = provider.research_account(_account())
    assert result.evidence == []


# --- failure classification ----------------------------------------------

def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_rate_limit_is_retryable():
    response = httpx.Response(429, request=_fake_request())
    mock_create = MagicMock(side_effect=anthropic.RateLimitError("rate limited", response=response, body=None))
    provider = _provider_with_client(mock_create)

    with pytest.raises(RetryableError):
        provider.research_account(_account())


def test_timeout_is_retryable():
    mock_create = MagicMock(side_effect=anthropic.APITimeoutError(request=_fake_request()))
    provider = _provider_with_client(mock_create)

    with pytest.raises(RetryableError):
        provider.research_account(_account())


def test_connection_error_is_retryable():
    mock_create = MagicMock(
        side_effect=anthropic.APIConnectionError(message="connection failed", request=_fake_request())
    )
    provider = _provider_with_client(mock_create)

    with pytest.raises(RetryableError):
        provider.research_account(_account())


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_errors_are_retryable(status_code):
    response = httpx.Response(status_code, request=_fake_request())
    mock_create = MagicMock(
        side_effect=anthropic.APIStatusError("server error", response=response, body=None)
    )
    provider = _provider_with_client(mock_create)

    with pytest.raises(RetryableError):
        provider.research_account(_account())


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    response = httpx.Response(status_code, request=_fake_request())
    mock_create = MagicMock(
        side_effect=anthropic.APIStatusError("bad request", response=response, body=None)
    )
    provider = _provider_with_client(mock_create)

    with pytest.raises(NonRetryableError):
        provider.research_account(_account())


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        AnthropicWebSearchResearchProvider(api_key="")
