"""AnthropicWebSearchResearchProvider - the only file allowed to know
Claude's web_search tool request/response shape. Verified against
Anthropic's current API docs (2026-08-19): tool type
`web_search_20250305`, response content blocks of type `text`,
`server_tool_use`, and `web_search_tool_result` (see
https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool).

Prompting strategy: the system prompt instructs Claude to search first,
then respond with ONLY a JSON object ({"claims": [...]}) - no prose, no
markdown fences. If that final text block doesn't parse as valid JSON
(extractor.parse_claims returns None), we retry once with an explicit
"reply with only the JSON" nudge before falling back to "no evidence
extracted" - the roadmap's stated failure scenario for this step, not a
crash.
"""

from __future__ import annotations

from typing import Any, List, Optional

import anthropic

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account

from ..extractor import build_evidence, parse_claims
from ..interface import ResearchProvider, ResearchResult

DEFAULT_MODEL = "claude-sonnet-5"
MAX_SEARCHES_PER_ACCOUNT = 3
MAX_JSON_RETRIES = 1

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

SYSTEM_PROMPT = """You are a B2B sales research assistant. Given a company \
name and domain, use the web_search tool to find recent, concrete business \
signals a salesperson would care about: hiring activity, funding news, \
product launches, leadership changes, or other strategic developments \
from roughly the last 12 months.

Rules:
- Every claim you report MUST be grounded in a page you actually searched \
for and found via web_search this turn. Never state a fact you did not \
retrieve from a search result.
- If you find no genuinely relevant, recent information, return an empty \
claims list. Do not guess or pad the list with generic/stale information.
- Rate your confidence in each claim honestly: "high" for a clear, \
specific, recent fact; "medium" for something plausible but less certain \
or less recent; "low" for a weak or ambiguous signal.

After searching, your FINAL response must be ONLY a JSON object in this \
exact shape, with no other text before or after it and no markdown code \
fences:

{"claims": [{"claim": "<one sentence, specific>", "source_url": "<the exact URL you found this on>", "confidence": "high|medium|low"}]}
"""


class AnthropicWebSearchResearchProvider(ResearchProvider):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        max_searches: int = MAX_SEARCHES_PER_ACCOUNT,
        client: Optional[anthropic.Anthropic] = None,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicWebSearchResearchProvider requires a non-empty api_key")
        self._model = model
        self._max_searches = max_searches
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def research_account(self, account: Account) -> ResearchResult:
        messages = [
            {
                "role": "user",
                "content": (
                    f"Research {account.name} (domain: {account.domain}). "
                    "Find recent business signals relevant to a B2B sales "
                    "team, then respond with the JSON object described in "
                    "your instructions - nothing else."
                ),
            }
        ]

        response = self._call(messages)
        verified_urls = self._verified_urls(response)
        final_text = self._final_text(response)

        raw_claims = parse_claims(final_text) if final_text is not None else None

        attempts = 0
        while raw_claims is None and attempts < MAX_JSON_RETRIES:
            attempts += 1
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Reply with ONLY the JSON "
                        'object in the exact shape {"claims": [...]}, no '
                        "other text, no markdown fences."
                    ),
                }
            )
            response = self._call(messages)
            verified_urls |= self._verified_urls(response)
            final_text = self._final_text(response)
            raw_claims = parse_claims(final_text) if final_text is not None else None

        if raw_claims is None:
            # Fell back after retrying - "no evidence extracted," not a
            # crash, per this step's stated failure scenario.
            return ResearchResult(evidence=[], verified_urls=list(verified_urls), raw_response=None)

        evidence, dropped = build_evidence(account.id, raw_claims, verified_urls)
        return ResearchResult(
            evidence=evidence, verified_urls=list(verified_urls), dropped_claim_count=dropped
        )

    def _call(self, messages: List[dict]) -> Any:
        try:
            return self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": self._max_searches,
                    }
                ],
            )
        except anthropic.RateLimitError as exc:
            raise RetryableError(f"Anthropic rate limit: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise RetryableError(f"Anthropic request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise RetryableError(f"Anthropic connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            detail = f"Anthropic returned HTTP {exc.status_code}: {exc.message}"
            if exc.status_code in _RETRYABLE_STATUS_CODES:
                raise RetryableError(detail) from exc
            raise NonRetryableError(detail) from exc

    @staticmethod
    def _verified_urls(response: Any) -> set:
        urls = set()
        for block in response.content:
            if getattr(block, "type", None) == "web_search_tool_result":
                content = block.content
                if isinstance(content, list):
                    for result in content:
                        url = getattr(result, "url", None)
                        if url:
                            urls.add(url)
        return urls

    @staticmethod
    def _final_text(response: Any) -> Optional[str]:
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]
        if not text_blocks:
            return None
        return text_blocks[-1].text
