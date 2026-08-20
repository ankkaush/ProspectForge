"""General field-level validation, shared across the pipeline - the
roadmap's explicit note for this step is that these rules are meant to be
applied at write-time everywhere, not just here. This module is that
shared home; contact_enrichment/validators.py now re-exports from here
rather than defining its own copy (it was written first, in Step 13,
before this module existed).

Deliberately minimal: format checks only, not exhaustive RFC validation -
enough to catch the obviously-broken cases a provider or messy source data
produces.
"""

from __future__ import annotations

import re
from typing import List, Optional

from prospectforge.models import Account, Contact

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_PATTERN = re.compile(r"^https?://[^\s]+\.[^\s]+$")


def is_plausible_email(email: str) -> bool:
    return bool(email) and bool(_EMAIL_PATTERN.match(email.strip()))


def is_plausible_url(url: str) -> bool:
    return bool(url) and bool(_URL_PATTERN.match(url.strip()))


def validate_contact(contact: Contact) -> List[str]:
    """Returns a list of human-readable issues, never raises - validation
    here is informational (used to flag/log), not a hard gate. A contact
    with issues still exists; it's just flagged for whoever's reviewing
    the data."""

    issues: List[str] = []
    if contact.email and not is_plausible_email(contact.email):
        issues.append(f"email '{contact.email}' does not look like a valid email address")
    if contact.linkedin_url and not is_plausible_url(contact.linkedin_url):
        issues.append(f"linkedin_url '{contact.linkedin_url}' does not look like a valid URL")
    return issues


def validate_account(account: Account) -> List[str]:
    issues: List[str] = []
    if account.employee_count is not None and account.employee_count < 0:
        issues.append(f"employee_count is negative: {account.employee_count}")
    if not account.domain or "." not in account.domain:
        issues.append(f"domain '{account.domain}' does not look like a valid domain")
    return issues
