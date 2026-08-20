"""The single pipeline entry point.

CLI, the API, and (later) a scheduler all call this same function - it has
no idea which of the three invoked it. That's deliberate: it's what lets
manual, scheduled, and API-triggered runs coexist without the pipeline
itself branching on "who called me" (see docs/architecture.md and the
technical audit's answer to "is this an automation").

As of Step 16, start_run runs ten real stages in sequence: account
discovery, the cheap prefilter, enrichment, the full fit evaluation,
research, decision-maker discovery, contact enrichment, dedup,
qualification, then prioritization. Each stage reads accounts (or
contacts) at whatever status the previous stage left them in - see
prospectforge/models/enums.py's ACCOUNT_STATUS_TRANSITIONS and the
technical audit for why that's what makes a stage resumable rather than a
special case. Decision-maker discovery is the one exception to "status
tracks progress" - see people_discovery/service.py's module docstring for
why it uses the ProviderRecord audit trail as its completion marker
instead; contact enrichment and qualification are back to the normal
pattern, using ContactStatus / AccountStatus (RESEARCHED ->
QUALIFIED/NOT_QUALIFIED). Dedup and prioritization don't use a status at
all - both are pure DB logic re-scanning the whole dataset each run, not
a per-item provider call (see dedup/service.py, prioritization/service.py).

Discovery, prefilter, fit evaluation, and dedup failures still fail the
whole run (a bulk-fetch, config-load, evaluation-logic, or merge-logic
failure has no partial result to report). Enrichment, research, people
discovery, and contact enrichment are different: each calls its provider
once per account or contact, so one failure doesn't stop the batch - each
isolates failures internally and reports them in its summary, and the run
finishes as PARTIAL_SUCCESS (not FAILED) when some items failed one of
those stages but the stage itself ran. Qualification's AI-assisted
rationale step follows this same isolation - see qualification/service.py
for why a rationale failure never affects the (already-decided,
deterministic) qualification verdict itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.logging import log_context
from app.orm import RunORM
from prospectforge.contact_enrichment import ContactEnrichmentProvider, run_contact_enrichment
from prospectforge.dedup import run_dedup
from prospectforge.discovery import DiscoveryProvider, run_discovery
from prospectforge.enrichment import EnrichmentProvider, run_enrichment
from prospectforge.fit import run_full_evaluation, run_prefilter
from prospectforge.icp.loader import ICPConfigError
from prospectforge.models.enums import RunStatus
from prospectforge.people_discovery import PersonDiscoveryProvider, run_people_discovery
from prospectforge.prioritization import run_prioritization
from prospectforge.qualification import RationaleProvider, run_qualification
from prospectforge.research import ResearchProvider, run_research

logger = logging.getLogger("prospectforge.trigger")


def start_run(
    icp_config_id: str,
    session: Session,
    *,
    discovery_provider: Optional[DiscoveryProvider] = None,
    enrichment_provider: Optional[EnrichmentProvider] = None,
    research_provider: Optional[ResearchProvider] = None,
    person_discovery_provider: Optional[PersonDiscoveryProvider] = None,
    contact_enrichment_provider: Optional[ContactEnrichmentProvider] = None,
    rationale_provider: Optional[RationaleProvider] = None,
    prioritization_weights: Optional[Dict[str, float]] = None,
) -> RunORM:
    run = RunORM(icp_config_id=icp_config_id, status=RunStatus.RUNNING)
    session.add(run)
    session.flush()  # assigns run.id without needing a full commit yet

    stages_run: list = []
    summary: dict = {}

    with log_context(run_id=str(run.id)):
        logger.info("run started for icp_config_id=%s", icp_config_id)

        try:
            summary["discovery"] = run_discovery(
                run.id, icp_config_id, session, provider=discovery_provider
            )
            stages_run.append("discovery")

            summary["prefilter"] = run_prefilter(run.id, icp_config_id, session)
            stages_run.append("prefilter")

            summary["enrichment"] = run_enrichment(run.id, session, provider=enrichment_provider)
            stages_run.append("enrichment")

            summary["fit_evaluation"] = run_full_evaluation(run.id, icp_config_id, session)
            stages_run.append("fit_evaluation")

            summary["research"] = run_research(run.id, session, provider=research_provider)
            stages_run.append("research")

            summary["people_discovery"] = run_people_discovery(
                run.id, session, provider=person_discovery_provider
            )
            stages_run.append("people_discovery")

            summary["contact_enrichment"] = run_contact_enrichment(
                run.id, session, provider=contact_enrichment_provider
            )
            stages_run.append("contact_enrichment")

            summary["dedup"] = run_dedup(run.id, session)
            stages_run.append("dedup")

            summary["qualification"] = run_qualification(
                run.id, session, provider=rationale_provider
            )
            stages_run.append("qualification")

            summary["prioritization"] = run_prioritization(
                run.id, session, weights=prioritization_weights
            )
            stages_run.append("prioritization")

            has_per_item_failures = (
                summary["enrichment"]["enrichment_failed"] > 0
                or summary["research"]["research_failed"] > 0
                or summary["people_discovery"]["search_failed"] > 0
                or summary["contact_enrichment"]["enrichment_failed"] > 0
            )
            run.status = RunStatus.PARTIAL_SUCCESS if has_per_item_failures else RunStatus.COMPLETED
        except ICPConfigError as exc:
            run.status = RunStatus.FAILED
            summary["error"] = str(exc)
            summary["failed_stage"] = "load_icp"
            logger.error("run failed loading ICP config: %s", exc)
        except Exception as exc:  # noqa: BLE001 - run-level failure, deliberately broad
            run.status = RunStatus.FAILED
            summary["error"] = str(exc)
            summary["failed_stage"] = _next_stage(stages_run)
            logger.error("run failed during %s: %s", summary["failed_stage"], exc)

        run.summary = {"stages_run": stages_run, **summary}
        run.completed_at = datetime.now(timezone.utc)
        logger.info("run finished with status=%s summary=%s", run.status, run.summary)

    session.flush()
    return run


def _next_stage(stages_run: list) -> str:
    pipeline = [
        "discovery",
        "prefilter",
        "enrichment",
        "fit_evaluation",
        "research",
        "people_discovery",
        "contact_enrichment",
        "dedup",
        "qualification",
        "prioritization",
    ]
    for stage in pipeline:
        if stage not in stages_run:
            return stage
    return "unknown"  # pragma: no cover - all stages already completed
