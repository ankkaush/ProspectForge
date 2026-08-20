"""Conversions between the Step 4 pydantic domain contracts and the Step 5
SQLAlchemy ORM rows. Kept as small, explicit functions rather than something
"automatic" - the two layers are allowed to drift slightly (e.g. ORM rows
carry real datetimes, pydantic Account stores them as ISO strings) and an
explicit mapper is the place that difference is handled once, deliberately.
"""

from __future__ import annotations

from app.orm import AccountORM, ContactORM
from prospectforge.models import Account, Contact


def account_to_orm(account: Account) -> AccountORM:
    return AccountORM(
        id=account.id,
        domain=account.domain,
        name=account.name,
        industry=account.industry,
        employee_count=account.employee_count,
        geography=account.geography,
        tech_stack=account.tech_stack,
        funding_stage=account.funding_stage,
        growth_signal=account.growth_signal,
        status=account.status,
        fit_tier=account.fit_tier,
        discovered_in_run_id=account.discovered_in_run_id,
    )


def orm_to_account(orm: AccountORM) -> Account:
    return Account(
        id=orm.id,
        domain=orm.domain,
        name=orm.name,
        industry=orm.industry,
        employee_count=orm.employee_count,
        geography=orm.geography,
        tech_stack=orm.tech_stack,
        funding_stage=orm.funding_stage,
        growth_signal=orm.growth_signal,
        status=orm.status,
        fit_tier=orm.fit_tier,
        discovered_in_run_id=orm.discovered_in_run_id,
        created_at=orm.created_at.isoformat(),
        updated_at=orm.updated_at.isoformat(),
    )


def contact_to_orm(contact: Contact) -> ContactORM:
    return ContactORM(
        id=contact.id,
        account_id=contact.account_id,
        name=contact.name,
        title=contact.title,
        seniority=contact.seniority,
        department=contact.department,
        email=contact.email,
        email_confidence=contact.email_confidence,
        linkedin_url=contact.linkedin_url,
        status=contact.status,
        # erased_at intentionally excluded, same as created_at/updated_at
        # below - a timestamp column, not something a caller constructing
        # a new Contact object sets directly.
    )


def orm_to_contact(orm: ContactORM) -> Contact:
    return Contact(
        id=orm.id,
        account_id=orm.account_id,
        name=orm.name,
        title=orm.title,
        seniority=orm.seniority,
        department=orm.department,
        email=orm.email,
        email_confidence=orm.email_confidence,
        linkedin_url=orm.linkedin_url,
        status=orm.status,
        erased_at=orm.erased_at.isoformat() if orm.erased_at else None,
        created_at=orm.created_at.isoformat(),
        updated_at=orm.updated_at.isoformat(),
    )
