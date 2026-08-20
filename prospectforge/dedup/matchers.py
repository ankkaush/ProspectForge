"""Deterministic matching keys first, fuzzy name similarity as a
conservative fallback - no LLM, per the roadmap's explicit guidance that
this is a classic deterministic problem.

Two-tier matching, in priority order:
  1. Normalized domain (accounts) / normalized email (contacts) - an exact
     match here is essentially certain (a "www." prefix, or literally the
     same mailbox, is not a coincidence).
  2. Fuzzy name similarity (difflib.SequenceMatcher - stdlib, no new
     dependency) - only consulted when the deterministic key doesn't
     already resolve it, and gated behind a conservative threshold. This
     is the guard against the roadmap's named failure scenario:
     over-aggressive fuzzy matching merging two genuinely different
     companies (or two different people).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from app.orm import AccountORM, ContactORM

# Conservative on purpose - see module docstring. Chosen high enough that
# "Northstar Metrics" vs "Northstar Analytics" (a genuinely different
# company) does NOT match, while "Northstar Metrics" vs "Northstar
# Metrics Inc." does.
ACCOUNT_NAME_SIMILARITY_THRESHOLD = 0.90
CONTACT_NAME_SIMILARITY_THRESHOLD = 0.92

_LEGAL_SUFFIXES = re.compile(
    r"\s+(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|gmbh|co\.?)$", re.IGNORECASE
)


def normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.rstrip("/")


def normalize_company_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = _LEGAL_SUFFIXES.sub("", n).strip()
    return re.sub(r"\s+", " ", n)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_person_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def accounts_match_reason(a: AccountORM, b: AccountORM) -> Optional[str]:
    """Returns a human-readable reason if `a` and `b` are judged
    duplicates, else None. `a.domain != b.domain` is guaranteed by the
    accounts table's unique constraint, so a normalized-domain match here
    always means the raw strings differ in a superficial way (case,
    www., protocol, trailing slash)."""

    norm_a, norm_b = normalize_domain(a.domain), normalize_domain(b.domain)
    if norm_a == norm_b:
        return f"normalized domain match: '{a.domain}' ~ '{b.domain}'"

    name_a, name_b = normalize_company_name(a.name), normalize_company_name(b.name)
    similarity = _similarity(name_a, name_b)
    if similarity >= ACCOUNT_NAME_SIMILARITY_THRESHOLD:
        return f"name similarity {similarity:.2f} >= {ACCOUNT_NAME_SIMILARITY_THRESHOLD}: '{a.name}' ~ '{b.name}'"

    return None


def contacts_match_reason(a: ContactORM, b: ContactORM) -> Optional[str]:
    """Only ever meaningful for two contacts at the SAME account - a name
    or email match across two different companies is not a duplicate,
    it's two different people who happen to share a name. Callers
    (dedup/service.py) are responsible for that scoping."""

    if a.email and b.email:
        norm_a, norm_b = normalize_email(a.email), normalize_email(b.email)
        if norm_a == norm_b:
            return f"normalized email match: '{a.email}' ~ '{b.email}'"

    name_a, name_b = normalize_person_name(a.name), normalize_person_name(b.name)
    if name_a == name_b:
        return f"exact name match (case/whitespace variant): '{a.name}' ~ '{b.name}'"

    similarity = _similarity(name_a, name_b)
    if similarity >= CONTACT_NAME_SIMILARITY_THRESHOLD:
        return f"name similarity {similarity:.2f} >= {CONTACT_NAME_SIMILARITY_THRESHOLD}: '{a.name}' ~ '{b.name}'"

    return None
