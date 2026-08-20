"""Pure parsing/validation logic for turning a rationale-generation
response into rationale text - deliberately separate from
providers/anthropic_rationale.py's API-calling code, same split as Step
11's research/extractor.py, for the same reason: testable with plain
strings, no mocked API client needed.

The anti-hallucination check: each statement the model produces is tagged
with what it's based on - "fit", "contact", or "evidence:<uuid>". Any
statement citing an evidence id that isn't actually in this account's
evidence set is dropped, not trusted. "fit" and "contact" statements
describe data already given to the model in the prompt (not a claim about
the outside world), so they don't need the same per-id verification -
there's no id to check against.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional, Tuple
from uuid import UUID

logger = logging.getLogger("prospectforge.qualification")

_ALLOWED_BASES = {"fit", "contact", "general"}


def parse_statements(raw_text: str) -> Optional[List[dict]]:
    """Parses {"statements": [...]}. Returns None (not an exception) on
    any malformed input - the caller decides whether to retry or fall
    back to a templated rationale."""

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(parsed, dict) or "statements" not in parsed:
        return None
    statements = parsed["statements"]
    if not isinstance(statements, list):
        return None
    return statements


def build_rationale_text(
    raw_statements: List[dict], valid_evidence_ids: List[UUID]
) -> Tuple[Optional[str], int]:
    """Returns (rationale_text, dropped_count). rationale_text is None if
    every statement got dropped (or the input was empty) - the caller
    falls back to a templated rationale in that case."""

    valid_ids_str = {str(eid) for eid in valid_evidence_ids}
    kept: List[str] = []
    dropped = 0

    for statement in raw_statements:
        if not isinstance(statement, dict):
            dropped += 1
            continue
        text = statement.get("text")
        based_on = statement.get("based_on")
        if not text or not based_on:
            dropped += 1
            continue

        if isinstance(based_on, str) and based_on.startswith("evidence:"):
            evidence_id = based_on[len("evidence:") :]
            if evidence_id not in valid_ids_str:
                dropped += 1
                logger.info(
                    "dropped rationale statement - cites an evidence id not in this "
                    "account's evidence set: %s",
                    evidence_id,
                )
                continue
        elif based_on not in _ALLOWED_BASES:
            dropped += 1
            logger.info("dropped rationale statement - unrecognized based_on: %s", based_on)
            continue

        kept.append(text)

    if not kept:
        return None, dropped
    return " ".join(kept), dropped


def templated_rationale(reasons: List[str]) -> str:
    """The deterministic fallback - always available, no LLM involved.
    Used whenever the AI-generated rationale fails or every statement gets
    dropped, so a QualificationResult never ends up without some
    human-readable explanation."""

    return "; ".join(reasons) + "."
