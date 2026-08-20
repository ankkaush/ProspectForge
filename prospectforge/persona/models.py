"""The buyer-persona config schema - data-driven like the ICP (Step 6),
for the same reason: who counts as a relevant decision-maker is a business
judgment call that should be reviewable and swappable without a code
change, not hardcoded into the matching logic.

Matching is deterministic keyword matching, not AI - see
docs/adr/008-persona-matching-is-deterministic.md. A contact matches the
persona if their title contains at least one seniority keyword AND at
least one department keyword (both required - "VP" alone matches every VP
in the company, "Sales" alone matches an SDR).
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator


class PersonaConfig(BaseModel):
    id: str
    version: int
    name: str
    description: str

    seniority_keywords: List[str] = Field(default_factory=list)
    department_keywords: List[str] = Field(default_factory=list)

    @field_validator("seniority_keywords", "department_keywords")
    @classmethod
    def must_be_non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError(
                "seniority_keywords and department_keywords must both be non-empty - "
                "an empty list would match either everyone or no one, silently"
            )
        return v
