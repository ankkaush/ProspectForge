"""The deterministic qualification engine - status, reasons, and
confidence are decided entirely by rules here, with zero AI involvement.
Step 15's AI usage (rationale.py, providers/anthropic_rationale.py) only
phrases an already-complete verdict; it never runs before this module has
finished.

By the time an account reaches this engine (status=RESEARCHED), Step 11
has already routed Tier 3 / disqualified accounts straight to REJECTED -
every account here already cleared the fit bar (Tier 1, Tier 2, or
Insufficient Data, given the benefit of the doubt per Steps 8/10's
established principle). The two things left to resolve:

  1. Do we have someone to contact? No contact -> NOT_QUALIFIED. This is
     different from earlier "missing data" cases: Step 12's people search
     already ran and recorded its completion (see
     people_discovery/service.py's ProviderRecord marker) - a company with
     zero matching contacts isn't "not yet looked at," it's "looked, and
     there's no one to reach right now." Parking it (NOT_QUALIFIED,
     terminal) is the honest outcome, not a punishment for a data gap.
  2. How confident are we? Tier, evidence presence, and this specific
     contact's email quality all feed a transparent, additive confidence
     score - never a black box.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from app.orm import ContactORM, EvidenceORM
from prospectforge.models import QualificationResult
from prospectforge.models.enums import FitTier, QualificationStatus

TIER_BASE_CONFIDENCE = {
    FitTier.TIER_1: 0.85,
    FitTier.TIER_2: 0.65,
    FitTier.INSUFFICIENT_DATA: 0.35,
}
EVIDENCE_BONUS = 0.10
VERIFIED_EMAIL_BONUS = 0.05
MAX_CONFIDENCE = 0.98


def qualify_account(
    account_id: uuid.UUID,
    fit_tier: Optional[FitTier],
    fit_reasons: List[str],
    evidence: List[EvidenceORM],
    contacts: List[ContactORM],
) -> List[QualificationResult]:
    evidence_ids = [e.id for e in evidence]

    if not contacts:
        return [
            _make_result(
                account_id,
                None,
                QualificationStatus.NOT_QUALIFIED,
                reasons=["No decision-maker contact was found for this account."] + _fit_summary(fit_tier, fit_reasons),
                confidence=0.0,
                evidence_ids=evidence_ids,
            )
        ]

    if fit_tier in (FitTier.TIER_3, FitTier.REJECTED, None):
        # Defensive - Step 11 shouldn't route these here, but a
        # QualificationResult must still exist per contact rather than
        # silently skipping them.
        reasons = ["Fit tier does not meet the qualification bar."] + _fit_summary(fit_tier, fit_reasons)
        return [
            _make_result(account_id, c.id, QualificationStatus.NOT_QUALIFIED, reasons, 0.0, evidence_ids)
            for c in contacts
        ]

    base_confidence = TIER_BASE_CONFIDENCE.get(fit_tier, 0.35)
    results = []
    for contact in contacts:
        confidence = base_confidence
        reasons = _fit_summary(fit_tier, fit_reasons)
        reasons.append(f"Candidate decision-maker: {contact.name} ({contact.title or 'title unknown'})")

        if evidence:
            confidence = min(confidence + EVIDENCE_BONUS, MAX_CONFIDENCE)
            reasons.append(f"{len(evidence)} sourced evidence item(s) found")
        else:
            reasons.append("No recent evidence found - qualification based on fit and contact availability alone")

        if contact.email_confidence == "verified":
            confidence = min(confidence + VERIFIED_EMAIL_BONUS, MAX_CONFIDENCE)
            reasons.append("Contact email is verified")
        elif contact.email_confidence == "unverified":
            reasons.append("Contact email is unverified")
        else:
            reasons.append("No verified email on file for this contact")

        if fit_tier == FitTier.INSUFFICIENT_DATA:
            reasons.insert(0, "Fit could not be fully confirmed - missing post-enrichment data")

        results.append(
            _make_result(account_id, contact.id, QualificationStatus.QUALIFIED, reasons, confidence, evidence_ids)
        )

    return results


def _fit_summary(fit_tier: Optional[FitTier], fit_reasons: List[str]) -> List[str]:
    if fit_tier is None:
        return ["No fit evaluation found for this account."]
    label = f"Fit tier: {fit_tier.value}"
    return [label] + list(fit_reasons)


def _make_result(
    account_id: uuid.UUID,
    contact_id: Optional[uuid.UUID],
    status: QualificationStatus,
    reasons: List[str],
    confidence: float,
    evidence_ids: List[uuid.UUID],
) -> QualificationResult:
    return QualificationResult(
        account_id=account_id,
        contact_id=contact_id,
        status=status,
        reasons=reasons,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )
