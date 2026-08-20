"""Orchestrates contact enrichment for a run: finds contacts waiting at
status=DISCOVERED (first attempt) or ENRICHMENT_FAILED (retry - fixed at
Step 19's idempotency review, same orphaning bug found in account
enrichment and research: this query originally only looked at DISCOVERED)
and enriches each independently - symmetric to Step 9's account
enrichment, including per-item failure isolation (one contact's failed
call doesn't stop the batch).

PII handling (this step's other stated purpose - see
docs/adr/009-contact-pii-handling.md for the full reasoning): every log
line in this module references a contact by id (and, for readability,
name - already logged in plaintext since Step 12) but NEVER by email. The
email itself is only ever written to the database (email/email_confidence
columns) and to the ProviderRecord audit payload - never to a log line.
This is verified directly in test_contact_enrichment_logging.py by
grepping real formatted log output for a known email string.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.mappers import orm_to_account, orm_to_contact
from app.orm import AccountORM, ContactORM, ProviderRecordORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import ContactStatus

from .interface import ContactEnrichmentProvider

logger = logging.getLogger("prospectforge.contact_enrichment")


def get_default_contact_enrichment_provider() -> ContactEnrichmentProvider:
    """Factory for the provider used when the caller doesn't inject one -
    same pattern as every other stage's default-provider factory."""

    provider_name = get_settings().contact_enrichment_provider

    if provider_name == "csv":
        from .providers.csv_provider import CsvContactEnrichmentProvider

        return CsvContactEnrichmentProvider()

    if provider_name == "apollo":
        from .providers.apollo import ApolloContactEnrichmentProvider

        api_key = get_settings().apollo_api_key
        if not api_key:
            raise RuntimeError(
                "APOLLO_API_KEY is not set - required to run contact enrichment "
                "against the real Apollo provider."
            )
        return ApolloContactEnrichmentProvider(api_key=api_key)

    raise RuntimeError(
        f"Unknown CONTACT_ENRICHMENT_PROVIDER='{provider_name}' - expected 'csv' or 'apollo'."
    )


def run_contact_enrichment(
    run_id: uuid.UUID,
    session: Session,
    *,
    provider: Optional[ContactEnrichmentProvider] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        active_provider = provider or get_default_contact_enrichment_provider()
        # The actual configured provider, not a hardcoded "apollo" - same
        # audit-trail mislabeling bug found and fixed at Step 26 across
        # every csv/apollo-switchable stage (see discovery/service.py's
        # comment for the full story).
        provider_label = get_settings().contact_enrichment_provider

        summary = {
            "evaluated": 0,
            "enriched": 0,
            "no_data_found": 0,
            "invalid_email": 0,
            "enrichment_failed": 0,
        }

        discovered_contacts = (
            session.query(ContactORM)
            .filter(ContactORM.status.in_([ContactStatus.DISCOVERED, ContactStatus.ENRICHMENT_FAILED]))
            .all()
        )

        for contact_orm in discovered_contacts:
            with log_context(account_id=str(contact_orm.account_id), contact_id=str(contact_orm.id)):
                summary["evaluated"] += 1
                account_orm = session.get(AccountORM, contact_orm.account_id)
                contact = orm_to_contact(contact_orm)
                account = orm_to_account(account_orm)

                try:
                    result = call_with_retry(
                        lambda: active_provider.enrich_contact(contact, account),
                        session=session,
                        run_id=run_id,
                        account_id=contact_orm.account_id,
                        contact_id=contact_orm.id,
                        provider=provider_label,
                        operation="contact_enrichment",
                    )
                except (RetryableError, NonRetryableError) as exc:
                    contact_orm.status = ContactStatus.ENRICHMENT_FAILED
                    summary["enrichment_failed"] += 1
                    # Never include the exception's raw text if a provider
                    # ever echoed the request back in an error message -
                    # log only that it failed, not any payload contents.
                    logger.info("contact enrichment failed for contact_id=%s", contact_orm.id)
                    session.flush()
                    continue

                session.add(
                    ProviderRecordORM(
                        account_id=contact_orm.account_id,
                        contact_id=contact_orm.id,
                        provider=provider_label,
                        operation="contact_enrichment",
                        payload=result.raw_payload,
                    )
                )

                if result.found:
                    contact_orm.email = result.email
                    contact_orm.email_confidence = result.email_confidence
                    contact_orm.seniority = result.seniority
                    contact_orm.linkedin_url = result.linkedin_url
                    if result.email_confidence == "invalid":
                        summary["invalid_email"] += 1
                    summary["enriched"] += 1
                else:
                    summary["no_data_found"] += 1

                contact_orm.status = ContactStatus.ENRICHED

                # Deliberately no %s for email anywhere in this line -
                # confidence and found-state are useful signal, the email
                # value itself is not something a log line needs.
                logger.info(
                    "enriched contact_id=%s name=%s found=%s email_confidence=%s",
                    contact_orm.id,
                    contact_orm.name,
                    result.found,
                    result.email_confidence,
                )
                session.flush()

        logger.info("contact enrichment completed with summary=%s", summary)
        return summary
