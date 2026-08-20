"""CsvDiscoveryProvider - a DiscoveryProvider backed by a local CSV file
instead of a live search API.

Why this exists: ADR-003 assumed Apollo's free tier would support account
discovery. A live check against a real Apollo API key (2026-08-19) showed
the free plan has no access to mixed_companies/search (or mixed_people/
search) at all - "not accessible, even with a master key" - regardless of
credits. See ADR-003's addendum for the full finding.

This provider fills the same DiscoveryCriteria-in, DiscoveryPage-out
contract as ApolloDiscoveryProvider, applying the same three criteria
(industry, employee count range, geography) as an in-process filter over
CSV rows - the same filtering a live search API would do server-side, kept
here so the rest of the pipeline behaves identically regardless of which
provider is active. This is the concrete proof of ADR-005's boundary
claim: swapping the active provider (see discovery/service.py's
get_default_discovery_provider) touches provider selection only, not
discovery/service.py's orchestration logic, criteria.py, or anything
downstream.
"""

from __future__ import annotations

import csv
import uuid
from pathlib import Path
from typing import List, Optional

from prospectforge.models import Account

from ..interface import DiscoveredOrganization, DiscoveryCriteria, DiscoveryPage, DiscoveryProvider

DEFAULT_CSV_PATH = Path(__file__).parent.parent / "seed_data" / "saas_fictional_accounts.csv"


class CsvDiscoveryProvider(DiscoveryProvider):
    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self._csv_path = csv_path or DEFAULT_CSV_PATH
        if not self._csv_path.exists():
            raise FileNotFoundError(f"CSV discovery seed file not found: {self._csv_path}")

    def search_accounts(
        self, criteria: DiscoveryCriteria, *, page: int, per_page: int
    ) -> DiscoveryPage:
        rows = self._read_rows()
        matching = [row for row in rows if self._matches(row, criteria)]

        start = (page - 1) * per_page
        page_rows = matching[start : start + per_page]
        total_entries = len(matching)
        total_pages = max(1, -(-total_entries // per_page))  # ceiling division

        organizations = [self._map_row(row) for row in page_rows]

        return DiscoveryPage(
            organizations=organizations,
            page=page,
            total_pages=total_pages,
            total_entries=total_entries,
        )

    def _read_rows(self) -> List[dict]:
        with self._csv_path.open(newline="") as f:
            return list(csv.DictReader(f))

    @staticmethod
    def _matches(row: dict, criteria: DiscoveryCriteria) -> bool:
        if criteria.industries and row.get("industry") not in criteria.industries:
            return False

        if row.get("employee_count"):
            employee_count = int(row["employee_count"])
            if criteria.employee_count_min is not None and employee_count < criteria.employee_count_min:
                return False
            if criteria.employee_count_max is not None and employee_count > criteria.employee_count_max:
                return False

        if criteria.geographies and row.get("country") not in criteria.geographies:
            return False

        return True

    @staticmethod
    def _map_row(row: dict) -> DiscoveredOrganization:
        domain = (row.get("domain") or "").strip()
        if not domain:
            return DiscoveredOrganization(
                account=None,
                raw_payload=dict(row),
                skip_reason="no domain in CSV row - cannot dedupe or identify this account",
            )

        # Account.geography holds the country alone, not "city, state,
        # country" - the ICP's geography criterion does an exact match
        # against country names (Step 6's seed config), so a compound
        # string would never match even a genuinely supported location.
        # City/state detail isn't lost - it's still in raw_payload for
        # anything that needs it later.
        geography = row.get("country") or None

        account = Account(
            id=uuid.uuid4(),
            domain=domain,
            name=row["name"],
            industry=row.get("industry") or None,
            employee_count=int(row["employee_count"]) if row.get("employee_count") else None,
            geography=geography,
        )
        return DiscoveredOrganization(account=account, raw_payload=dict(row), skip_reason=None)
