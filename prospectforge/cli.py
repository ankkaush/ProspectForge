"""Command-line entry point.

Proves the "manual trigger" side of the trigger design: this calls the
exact same app.trigger.start_run function the API's POST /runs endpoint
calls, just from a different caller. Neither the pipeline nor start_run
itself has any idea which one invoked it.

Usage:
    python -m prospectforge.cli start-run --icp-config-id saas-fictional-v1
    python -m prospectforge.cli init-db
    python -m prospectforge.cli review-queue
    python -m prospectforge.cli approve --prospect-id <uuid>
    python -m prospectforge.cli reject --prospect-id <uuid> --reason "..."
    python -m prospectforge.cli reject-all-pending --reason "..."
    python -m prospectforge.cli review-report
    python -m prospectforge.cli sync-to-crm
    python -m prospectforge.cli erase-contact --contact-id <uuid>
    python -m prospectforge.cli run-summary --run-id <uuid>
    python -m prospectforge.cli check-provider-health --provider apollo
"""

from __future__ import annotations

import argparse
import sys
import uuid

from app.db import session_scope
from app.logging import configure_logging
from app.config import get_settings
from infra.observability import init_observability


def cmd_start_run(icp_config_id: str) -> None:
    from app.trigger import start_run

    with session_scope() as db:
        run = start_run(icp_config_id, db)
        print(f"Run {run.id} finished with status={run.status}, summary={run.summary}")


def cmd_review_queue() -> None:
    from prospectforge.review import list_pending_review

    with session_scope() as db:
        queue = list_pending_review(db)
        if not queue:
            print("Review queue is empty.")
            return
        for item in queue:
            print(
                f"[rank {item['priority_rank']}] {item['prospect_record_id']} - "
                f"{item['account_name']} ({item['account_domain']}) - "
                f"{item['contact_name']}, {item['contact_title']} - "
                f"confidence={item['qualification_confidence']:.2f}\n"
                f"    {item['rationale_text']}"
            )


def cmd_approve(prospect_id: str) -> None:
    from prospectforge.review import ReviewError, approve_prospect

    with session_scope() as db:
        try:
            record = approve_prospect(uuid.UUID(prospect_id), db)
        except ReviewError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Approved {record.id}")


def cmd_reject(prospect_id: str, reason: str) -> None:
    from prospectforge.review import ReviewError, reject_prospect

    with session_scope() as db:
        try:
            record = reject_prospect(uuid.UUID(prospect_id), db, reason=reason)
        except ReviewError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Rejected {record.id}: {record.review_reason}")


def cmd_reject_all_pending(reason: str) -> None:
    from prospectforge.review import ReviewError, bulk_reject_pending

    with session_scope() as db:
        try:
            count = bulk_reject_pending(db, reason=reason)
        except ReviewError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Rejected {count} pending record(s): {reason}")


def cmd_sync_to_crm() -> None:
    from prospectforge.crm import run_crm_sync

    with session_scope() as db:
        summary = run_crm_sync(db)
        print(f"Synced {summary['synced']} of {summary['evaluated']} approved prospect(s)")
        if summary["skipped_no_email"]:
            print(f"Skipped (no email on file): {summary['skipped_no_email']}")
        if summary["sync_failed"]:
            print(f"Failed: {summary['sync_failed']} (see logs)")


def cmd_erase_contact(contact_id: str) -> None:
    from prospectforge.privacy import erase_contact
    from prospectforge.privacy.erasure import ErasureError

    with session_scope() as db:
        try:
            erase_contact(uuid.UUID(contact_id), db)
        except ErasureError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Erased contact {contact_id}")


def cmd_run_summary(run_id: str) -> None:
    """The "one dashboard/log query" view Step 22's exit criteria asks
    for: did the last run work, and if not, where did it fail - read
    straight off the Run row's own summary, not a second, separately
    maintained metrics record."""

    from app.orm import RunORM

    with session_scope() as db:
        run = db.get(RunORM, uuid.UUID(run_id))
        if run is None:
            print(f"Error: no run found with id={run_id}")
            sys.exit(1)

        print(f"Run {run.id} - status={run.status.value if hasattr(run.status, 'value') else run.status}")
        print(f"  started_at={run.started_at}  completed_at={run.completed_at}")
        if run.summary.get("error"):
            print(f"  FAILED at stage={run.summary.get('failed_stage')}: {run.summary['error']}")
            return

        stage_count_keys = {
            "discovery": "persisted_new",
            "prefilter": "advanced",
            "enrichment": "enriched",
            "fit_evaluation": "tier_1",
            "research": "researched",
            "people_discovery": "contacts_found",
            "contact_enrichment": "enriched",
            "dedup": "accounts_merged",
            "qualification": "accounts_qualified",
            "prioritization": "prospects_scored",
        }
        for stage in run.summary.get("stages_run", []):
            stage_summary = run.summary.get(stage, {})
            key = stage_count_keys.get(stage)
            headline = stage_summary.get(key) if key else None
            print(f"  {stage}: {headline if headline is not None else stage_summary}")


def cmd_check_provider_health(provider: str) -> None:
    from infra.observability import check_and_alert_on_failure_rate, compute_provider_failure_rate

    with session_scope() as db:
        stats = compute_provider_failure_rate(db, provider)
        if stats["total_attempts"] == 0:
            print(f"No attempts logged for provider={provider} in the last {stats['window_minutes']} minutes.")
            return
        print(
            f"provider={provider}: {stats['failed_attempts']}/{stats['total_attempts']} failed "
            f"({stats['failure_rate']:.0%}) in the last {stats['window_minutes']} minutes"
        )
        alert = check_and_alert_on_failure_rate(db, provider)
        if alert:
            print("ALERT: failure rate crossed the threshold (see logs / Sentry).")


def cmd_review_report() -> None:
    from prospectforge.review import review_report

    with session_scope() as db:
        report = review_report(db)
        print(
            f"Total: {report['total']}  Pending: {report['pending']}  "
            f"Approved: {report['approved']}  Rejected: {report['rejected']}"
        )
        if report["approval_rate"] is not None:
            print(
                f"Approval rate: {report['approval_rate']:.1%}  "
                f"Rejection rate: {report['rejection_rate']:.1%}"
            )
        else:
            print("No decisions recorded yet.")


def cmd_init_db() -> None:
    """Local-dev convenience: creates all tables directly from the ORM
    metadata, bypassing Alembic. Useful for a quick throwaway database (or
    this project's test suite); real schema changes over time should go
    through an Alembic migration instead, so the change is reviewable and
    reversible - init-db has no history and no downgrade path."""

    from app.db import get_engine
    from app.orm import Base

    Base.metadata.create_all(get_engine())
    print("Database tables created.")


def main() -> None:
    configure_logging(level=get_settings().log_level)
    init_observability()

    parser = argparse.ArgumentParser(prog="prospectforge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_run_parser = subparsers.add_parser("start-run")
    start_run_parser.add_argument("--icp-config-id", required=True)

    subparsers.add_parser("init-db")

    subparsers.add_parser("review-queue")

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--prospect-id", required=True)

    reject_parser = subparsers.add_parser("reject")
    reject_parser.add_argument("--prospect-id", required=True)
    reject_parser.add_argument("--reason", required=True)

    reject_all_parser = subparsers.add_parser("reject-all-pending")
    reject_all_parser.add_argument("--reason", required=True)

    subparsers.add_parser("review-report")

    subparsers.add_parser("sync-to-crm")

    erase_contact_parser = subparsers.add_parser("erase-contact")
    erase_contact_parser.add_argument("--contact-id", required=True)

    run_summary_parser = subparsers.add_parser("run-summary")
    run_summary_parser.add_argument("--run-id", required=True)

    check_provider_health_parser = subparsers.add_parser("check-provider-health")
    check_provider_health_parser.add_argument("--provider", required=True)

    args = parser.parse_args()

    if args.command == "start-run":
        cmd_start_run(args.icp_config_id)
    elif args.command == "init-db":
        cmd_init_db()
    elif args.command == "review-queue":
        cmd_review_queue()
    elif args.command == "approve":
        cmd_approve(args.prospect_id)
    elif args.command == "reject":
        cmd_reject(args.prospect_id, args.reason)
    elif args.command == "reject-all-pending":
        cmd_reject_all_pending(args.reason)
    elif args.command == "review-report":
        cmd_review_report()
    elif args.command == "sync-to-crm":
        cmd_sync_to_crm()
    elif args.command == "erase-contact":
        cmd_erase_contact(args.contact_id)
    elif args.command == "run-summary":
        cmd_run_summary(args.run_id)
    elif args.command == "check-provider-health":
        cmd_check_provider_health(args.provider)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
