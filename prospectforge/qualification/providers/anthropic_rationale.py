"""AnthropicRationaleProvider - a plain Claude Messages API call, no tools
(unlike Step 11's research provider - rationale generation only phrases
data already given to it, it never needs to search for anything).

Retries on malformed JSON exactly like Step 11's research provider (one
retry, then give up); unlike research, giving up here just means
returning rationale_text=None - the caller (qualification/service.py)
falls back to a templated rationale, since the actual qualification
verdict was already decided by the deterministic engine before this class
is ever called.
"""

from __future__ import annotations

from typing import Any, List, Optional

import anthropic

from infra.retry import NonRetryableError, RetryableError

from ..interface import RationaleContext, RationaleProvider, RationaleResult
from ..rationale import build_rationale_text, parse_statements

DEFAULT_MODEL = "claude-sonnet-5"
MAX_JSON_RETRIES = 1

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

SYSTEM_PROMPT = """You write a one-to-two sentence rationale explaining why a \
sales prospect was qualified, using ONLY the facts given to you. You do not \
decide whether the prospect is qualified - that decision has already been \
made; your only job is to phrase it clearly.

Rules:
- Never state a fact that was not given to you in the input.
- Every statement must be tagged with what it's based on: "fit" (the fit \
tier/reasons given), "contact" (the contact info given), or, if it restates \
a specific evidence item, exactly "evidence:<the id given for that item>". \
Never invent an evidence id.
- Keep it concise and concrete - this is read by a sales rep deciding who \
to contact next, not a general summary.

Respond with ONLY a JSON object in this exact shape, no other text and no \
markdown fences:

{"statements": [{"text": "<one clear sentence>", "based_on": "fit|contact|evidence:<id>"}]}
"""


class AnthropicRationaleProvider(RationaleProvider):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        client: Optional[anthropic.Anthropic] = None,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicRationaleProvider requires a non-empty api_key")
        self._model = model
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def generate_rationale(self, context: RationaleContext) -> RationaleResult:
        valid_evidence_ids = [e.id for e in context.evidence]
        messages = [{"role": "user", "content": self._build_prompt(context)}]

        response = self._call(messages)
        raw_statements = parse_statements(self._final_text(response))

        attempts = 0
        while raw_statements is None and attempts < MAX_JSON_RETRIES:
            attempts += 1
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": 'That was not valid JSON. Reply with ONLY the JSON '
                    'object in the exact shape {"statements": [...]}, no other text.',
                }
            )
            response = self._call(messages)
            raw_statements = parse_statements(self._final_text(response))

        if raw_statements is None:
            return RationaleResult(rationale_text=None, raw_response=None)

        text, dropped = build_rationale_text(raw_statements, valid_evidence_ids)
        return RationaleResult(rationale_text=text, dropped_statement_count=dropped)

    @staticmethod
    def _build_prompt(context: RationaleContext) -> str:
        evidence_lines = "\n".join(
            f"- id={e.id}, confidence={e.confidence}: {e.claim}" for e in context.evidence
        ) or "(none)"
        return (
            f"Account: {context.account_name}\n"
            f"Fit tier: {context.fit_tier}\n"
            f"Fit/qualification reasons: {'; '.join(context.deterministic_reasons)}\n"
            f"Evidence:\n{evidence_lines}\n"
            f"Contact: {context.contact_name or '(none)'} - {context.contact_title or 'title unknown'} "
            f"(email confidence: {context.contact_email_confidence or 'unknown'})\n"
        )

    def _call(self, messages: List[dict]) -> Any:
        try:
            return self._client.messages.create(
                model=self._model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=messages,
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
    def _final_text(response: Any) -> str:
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]
        return text_blocks[-1].text if text_blocks else ""
