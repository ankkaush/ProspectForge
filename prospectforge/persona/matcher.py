"""Deterministic title matching against a PersonaConfig - kept intentionally
simple (case-insensitive substring search), per the roadmap's explicit
guidance to try deterministic matching first and only reach for AI if title
normalization proves messy enough to need it. It hasn't, yet.
"""

from __future__ import annotations

from typing import List, Optional

from .models import PersonaConfig


def match_title_against_keywords(
    title: Optional[str], seniority_keywords: List[str], department_keywords: List[str]
) -> Optional[str]:
    """The actual matching primitive - takes plain keyword lists rather
    than a PersonaConfig, so provider adapters (which see a
    provider-independent PersonSearchCriteria, not a PersonaConfig) can
    reuse the exact same logic instead of re-implementing it. Returns a
    human-readable description of which keywords matched, or None if the
    title doesn't match on both required dimensions."""

    if not title:
        return None

    title_lower = title.lower()

    matched_seniority = next(
        (kw for kw in seniority_keywords if kw.lower() in title_lower), None
    )
    if matched_seniority is None:
        return None

    matched_department = next(
        (kw for kw in department_keywords if kw.lower() in title_lower), None
    )
    if matched_department is None:
        return None

    return f"seniority keyword '{matched_seniority}' + department keyword '{matched_department}'"


def match_title(title: Optional[str], persona: PersonaConfig) -> Optional[str]:
    """Convenience wrapper for callers that have a full PersonaConfig
    (e.g. people_discovery/service.py, before it's translated into
    provider-independent PersonSearchCriteria)."""

    return match_title_against_keywords(
        title, persona.seniority_keywords, persona.department_keywords
    )
