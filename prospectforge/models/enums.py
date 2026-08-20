"""Fixed vocabularies shared across ProspectForge's data contracts.

An enum is just a named list of the only values a field is allowed to hold —
e.g. an Account's status can only ever be one of the values in AccountStatus,
never an arbitrary string like "advnaced" (typo) or "kinda_done" (not a real
state). Pydantic checks this automatically wherever an enum is used as a
field type.
"""

from enum import Enum


class RunStatus(str, Enum):
    """Coarse, run-level summary of how a pipeline execution is going."""

    PENDING = "pending"
    RUNNING = "running"
    PARTIAL_SUCCESS = "partial_success"
    COMPLETED = "completed"
    FAILED = "failed"


class AccountStatus(str, Enum):
    """Where a single Account currently sits in the pipeline.

    This is the state machine the technical audit identified as missing:
    every account has exactly one current status, persisted immediately
    after each change. Resuming a crashed run means re-querying "accounts
    still at the status this stage expects as input" — nothing more
    elaborate than that.
    """

    RAW = "raw"
    ADVANCED = "advanced"
    REJECTED_EARLY = "rejected_early"
    ENRICHED = "enriched"
    ENRICHMENT_FAILED = "enrichment_failed"
    FIT_EVALUATED = "fit_evaluated"
    REJECTED = "rejected"
    RESEARCHED = "researched"
    RESEARCH_FAILED = "research_failed"
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    REVIEWED = "reviewed"
    SYNCED = "synced"


# The only legal next-states for each AccountStatus. Anything not listed
# here is an illegal transition and should be rejected rather than silently
# allowed - e.g. jumping straight from RAW to SYNCED would skip every gate
# (fit, enrichment, qualification, review) the pipeline exists to enforce.
ACCOUNT_STATUS_TRANSITIONS: dict[AccountStatus, set[AccountStatus]] = {
    AccountStatus.RAW: {AccountStatus.ADVANCED, AccountStatus.REJECTED_EARLY},
    AccountStatus.ADVANCED: {AccountStatus.ENRICHED, AccountStatus.ENRICHMENT_FAILED},
    AccountStatus.REJECTED_EARLY: set(),  # terminal
    AccountStatus.ENRICHED: {AccountStatus.FIT_EVALUATED},
    # a failed enrichment can be retried later by re-attempting enrichment,
    # which is the same transition as advancing for the first time
    AccountStatus.ENRICHMENT_FAILED: {AccountStatus.ENRICHED, AccountStatus.FIT_EVALUATED},
    AccountStatus.FIT_EVALUATED: {
        AccountStatus.RESEARCHED,
        AccountStatus.RESEARCH_FAILED,
        AccountStatus.REJECTED,
    },
    AccountStatus.REJECTED: set(),  # terminal
    # a failed research call can be retried later, same pattern as
    # ENRICHMENT_FAILED -> ENRICHED above
    AccountStatus.RESEARCH_FAILED: {AccountStatus.RESEARCHED},
    AccountStatus.RESEARCHED: {AccountStatus.QUALIFIED, AccountStatus.NOT_QUALIFIED},
    AccountStatus.NOT_QUALIFIED: set(),  # terminal
    AccountStatus.QUALIFIED: {AccountStatus.REVIEWED},
    AccountStatus.REVIEWED: {AccountStatus.SYNCED, AccountStatus.NOT_QUALIFIED},
    AccountStatus.SYNCED: set(),  # terminal
}


class ContactStatus(str, Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    ENRICHMENT_FAILED = "enrichment_failed"
    # Step 20 (GDPR right-to-erasure) - terminal. Deliberately excluded
    # from contact_enrichment/service.py's retry query (which only looks
    # at DISCOVERED/ENRICHMENT_FAILED): without this, Step 19's own
    # orphan-retry fix would re-fetch and silently repopulate an erased
    # contact's email on the very next enrichment run.
    ERASED = "erased"


class FitTier(str, Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    REJECTED = "rejected"
    INSUFFICIENT_DATA = "insufficient_data"


class FitPassType(str, Enum):
    """Which of the two fit evaluation passes produced a FitResult."""

    PREFILTER = "prefilter"  # step 8 - cheap, pre-enrichment
    FULL = "full"  # step 10 - complete, post-enrichment


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    NEEDS_MORE_INFO = "needs_more_info"


class EvidenceSourceType(str, Enum):
    """How a piece of Evidence was obtained - the provenance tag that lets
    us distinguish a verified fact from an AI-drawn inference everywhere
    downstream."""

    PROVIDER_API = "provider_api"
    AI_INFERRED = "ai_inferred"
    MANUAL = "manual"


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CallStatus(str, Enum):
    """Outcome of a single external call attempt, as logged in
    ExternalCallAttempt."""

    SUCCESS = "success"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_NON_RETRYABLE = "failed_non_retryable"
    FAILED_EXHAUSTED = "failed_exhausted"  # retries used up, giving up
    CIRCUIT_OPEN = "circuit_open"  # Step 19 - short-circuited, no network attempt made
