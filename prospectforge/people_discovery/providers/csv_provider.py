"""CsvPersonDiscoveryProvider - the active default PersonDiscoveryProvider,
for the same reason CsvDiscoveryProvider is the active default for account
discovery (ADR-003's addendum): Apollo's people-search endpoint returned
the identical 403 API_INACCESSIBLE on this project's Free plan when
re-checked live at the start of this step.

Applies the same in-process filtering a live search API would do
server-side: only rows matching the account's domain AND the persona
criteria are returned, mirroring CsvDiscoveryProvider's behavior exactly -
see that file for the same design note.
"""

from __future__ import annotations

import csv
import uuid
from pathlib import Path
from typing import List, Optional

from prospectforge.models import Account, Contact
from prospectforge.persona.matcher import match_title_against_keywords

from ..interface import DiscoveredPerson, PersonDiscoveryPage, PersonDiscoveryProvider, PersonSearchCriteria

DEFAULT_CSV_PATH = Path(__file__).parent.parent / "seed_data" / "contacts.csv"


class CsvPersonDiscoveryProvider(PersonDiscoveryProvider):
    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self._csv_path = csv_path or DEFAULT_CSV_PATH
        if not self._csv_path.exists():
            raise FileNotFoundError(f"CSV people-discovery seed file not found: {self._csv_path}")

    def search_people(
        self,
        account: Account,
        criteria: PersonSearchCriteria,
        *,
        page: int,
        per_page: int,
    ) -> PersonDiscoveryPage:
        rows = [r for r in self._read_rows() if r.get("account_domain") == account.domain]

        matched: List[DiscoveredPerson] = []
        for row in rows:
            matched_rule = match_title_against_keywords(
                row.get("title"), criteria.seniority_keywords, criteria.department_keywords
            )
            if matched_rule is None:
                continue  # doesn't match the persona - a live search API simply wouldn't return this person
            matched.append(self._map_row(row, account.id, matched_rule))

        start = (page - 1) * per_page
        page_people = matched[start : start + per_page]
        total_entries = len(matched)
        total_pages = max(1, -(-total_entries // per_page))

        return PersonDiscoveryPage(
            people=page_people, page=page, total_pages=total_pages, total_entries=total_entries
        )

    def _read_rows(self) -> List[dict]:
        with self._csv_path.open(newline="") as f:
            return list(csv.DictReader(f))

    @staticmethod
    def _map_row(row: dict, account_id: uuid.UUID, matched_rule: str) -> DiscoveredPerson:
        name = (row.get("name") or "").strip()
        if not name:
            return DiscoveredPerson(
                contact=None,
                raw_payload=dict(row),
                skip_reason="no name in CSV row - cannot identify this contact",
                matched_rule=matched_rule,
            )

        contact = Contact(
            id=uuid.uuid4(),
            account_id=account_id,
            name=name,
            title=row.get("title") or None,
            department=row.get("department") or None,
        )
        return DiscoveredPerson(contact=contact, raw_payload=dict(row), matched_rule=matched_rule)
