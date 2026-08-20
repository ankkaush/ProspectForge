"""SQLAlchemy table definitions - the actual database schema, generated
from and kept aligned with the Step 4 pydantic contracts.

Status/enum columns reuse the exact same Python enums defined in
prospectforge/models/enums.py (via SQLAlchemy's Enum type) rather than
redeclaring the vocabulary here - one definition of "what values can
Account.status hold," used by both the validation layer and the database.

Portability note: `JSON` is a cross-dialect SQLAlchemy type - it renders as
JSONB on PostgreSQL and as a portable equivalent on SQLite. This lets the
same schema run against real Postgres (via docker-compose, for actual use)
and against SQLite (for this project's fast local test suite) without two
separate schema definitions. The tradeoff - and it's a real one - is that
this test suite doesn't exercise Postgres-specific behavior (its native
UUID type, concurrent-transaction semantics). That gap is intentionally
closed later, at Step 21, which is scoped to test against real
infrastructure rather than a stand-in.

Note on typing: this project's current Python (3.9) doesn't support the
`X | None` union syntax at runtime, so `typing.Optional[X]` is used
throughout instead - functionally identical, just compatible further back.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from prospectforge.models.enums import (
    AccountStatus,
    CallStatus,
    ConfidenceLevel,
    ContactStatus,
    EvidenceSourceType,
    FitPassType,
    FitTier,
    QualificationStatus,
    ReviewDecision,
    RunStatus,
)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunORM(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    icp_config_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), default=RunStatus.PENDING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class AccountORM(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    geography: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tech_stack: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    funding_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    growth_signal: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, native_enum=False), default=AccountStatus.RAW
    )
    fit_tier: Mapped[Optional[FitTier]] = mapped_column(
        Enum(FitTier, native_enum=False), nullable=True
    )
    discovered_in_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ContactORM(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    seniority: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_confidence: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[ContactStatus] = mapped_column(
        Enum(ContactStatus, native_enum=False), default=ContactStatus.DISCOVERED
    )
    # Step 20 (GDPR right-to-erasure, Article 17): set once this contact's
    # personal-identifying fields have been scrubbed by
    # prospectforge/privacy/erasure.py - see that module's docstring for
    # exactly which fields count and why. NULL means never erased.
    erased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvidenceORM(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    claim: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[EvidenceSourceType] = mapped_column(
        Enum(EvidenceSourceType, native_enum=False), nullable=False
    )
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, native_enum=False), nullable=False
    )
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProviderRecordORM(Base):
    __tablename__ = "provider_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FitResultORM(Base):
    __tablename__ = "fit_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    pass_type: Mapped[FitPassType] = mapped_column(
        Enum(FitPassType, native_enum=False), nullable=False
    )
    tier: Mapped[FitTier] = mapped_column(Enum(FitTier, native_enum=False), nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QualificationResultORM(Base):
    __tablename__ = "qualification_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    status: Mapped[QualificationStatus] = mapped_column(
        Enum(QualificationStatus, native_enum=False), nullable=False
    )
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    rationale_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProspectRecordORM(Base):
    __tablename__ = "prospect_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    qualification_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qualification_results.id"), nullable=False
    )
    priority_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    priority_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review_decision: Mapped[ReviewDecision] = mapped_column(
        Enum(ReviewDecision, native_enum=False), default=ReviewDecision.PENDING
    )
    review_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crm_object_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ExternalCallAttemptORM(Base):
    __tablename__ = "external_call_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Nullable as of Step 18: CRM sync calls call_with_retry without a Run
    # to attribute them to (see infra/retry.py's docstring) - every
    # pipeline-stage caller still always provides one.
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("runs.id"), nullable=True)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CallStatus] = mapped_column(
        Enum(CallStatus, native_enum=False), nullable=False
    )
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    responded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
