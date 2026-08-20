"""CsvContactEnrichmentProvider - the active default ContactEnrichmentProvider,
same reason as every other CSV stand-in in this project (ADR-003's
addendum): Apollo's people/match endpoint returned the identical 403
API_INACCESSIBLE on this project's Free plan when checked live at the
start of this step.

Applies the same validation a real provider integration must apply
regardless of source: an implausible email format is never passed through
as a usable fact, even if that's literally what the row said - see
validators.py.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

from prospectforge.models import Account, Contact

from ..interface import ContactEnrichmentProvider, ContactEnrichmentResult
from ..validators import is_plausible_email

DEFAULT_CSV_PATH = Path(__file__).parent.parent / "seed_data" / "contact_enrichment.csv"


class CsvContactEnrichmentProvider(ContactEnrichmentProvider):
    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self._csv_path = csv_path or DEFAULT_CSV_PATH
        if not self._csv_path.exists():
            raise FileNotFoundError(f"CSV contact-enrichment seed file not found: {self._csv_path}")
        self._rows_by_key = self._index_rows()

    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        row = self._rows_by_key.get((contact.name, account.domain))
        if row is None:
            return ContactEnrichmentResult(found=False, raw_payload={})

        email = (row.get("email") or "").strip()
        email_confidence = row.get("email_confidence") or None

        if not email:
            # The row exists but has no email on file - found=True (we do
            # have a record), just no email in it.
            return ContactEnrichmentResult(
                found=True,
                seniority=row.get("seniority") or None,
                linkedin_url=row.get("linkedin_url") or None,
                raw_payload=dict(row),
            )

        if not is_plausible_email(email):
            # Never store a malformed email as a usable fact, regardless
            # of what confidence the source data claimed for it.
            return ContactEnrichmentResult(
                found=True,
                email=None,
                email_confidence="invalid",
                seniority=row.get("seniority") or None,
                linkedin_url=row.get("linkedin_url") or None,
                raw_payload=dict(row),
            )

        return ContactEnrichmentResult(
            found=True,
            email=email,
            email_confidence=email_confidence,
            seniority=row.get("seniority") or None,
            linkedin_url=row.get("linkedin_url") or None,
            raw_payload=dict(row),
        )

    def _index_rows(self) -> Dict[tuple, dict]:
        with self._csv_path.open(newline="") as f:
            rows: List[dict] = list(csv.DictReader(f))
        return {(row["name"], row["account_domain"]): row for row in rows}
