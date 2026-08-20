"""Pure parsing/validation logic for turning a Claude response's final
text block into Evidence objects - deliberately separate from
providers/anthropic_web_search.py's API-calling code, so this logic is
testable with plain strings, no mocked API client needed.

The anti-hallucination check this module exists for: pydantic validation
(and even a well-formed JSON claim) only proves a claim is well-shaped, not
that it's true - a model can assert a source_url that sounds plausible but
that it never actually retrieved. `build_evidence` cross-checks every
claimed source_url against `verified_urls` - the set of URLs the web_search
tool actually returned in that same turn - and drops anything that doesn't
match. This is a concrete, structural check, not just "trust the model
when it says confidence: high."
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, List, Optional

from prospectforge.models import ConfidenceLevel, Evidence, EvidenceSourceType

logger = logging.getLogger("prospectforge.research")


def parse_claims(raw_text: str) -> Optional[List[dict]]:
    """Parses Claude's final text block as {"claims": [...]}. Returns None
    (not an exception) on any malformed input - the caller decides whether
    to retry or fall back to 'no evidence extracted'."""

    text = raw_text.strip()
    # tolerate a model wrapping the JSON in a markdown code fence despite
    # being told not to - a cheap, common failure mode worth handling
    # rather than treating as a hard parse failure
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(parsed, dict) or "claims" not in parsed:
        return None
    claims = parsed["claims"]
    if not isinstance(claims, list):
        return None
    return claims


def build_evidence(
    account_id: uuid.UUID, raw_claims: List[dict], verified_urls: set
) -> tuple[List[Evidence], int]:
    """Validates and converts raw claim dicts into Evidence objects.
    Returns (evidence_list, dropped_count). A claim is dropped (not
    crashed on) if it's missing a required field, has an invalid
    confidence value, or cites a source_url outside verified_urls."""

    evidence: List[Evidence] = []
    dropped = 0

    for raw in raw_claims:
        claim_text = raw.get("claim") if isinstance(raw, dict) else None
        source_url = raw.get("source_url") if isinstance(raw, dict) else None
        confidence_raw = raw.get("confidence") if isinstance(raw, dict) else None

        if not claim_text or not source_url or not confidence_raw:
            dropped += 1
            logger.info("dropped claim - missing required field: %s", raw)
            continue

        try:
            confidence = ConfidenceLevel(confidence_raw)
        except ValueError:
            dropped += 1
            logger.info("dropped claim - invalid confidence value: %s", confidence_raw)
            continue

        if source_url not in verified_urls:
            dropped += 1
            logger.info(
                "dropped claim - cites a source_url not actually retrieved this turn: %s",
                source_url,
            )
            continue

        evidence.append(
            Evidence(
                account_id=account_id,
                claim=claim_text,
                source_type=EvidenceSourceType.AI_INFERRED,
                source_url=source_url,
                confidence=confidence,
            )
        )

    return evidence, dropped
