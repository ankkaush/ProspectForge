"""Structured JSON logging with automatic run_id / account_id / contact_id
correlation.

The problem this solves: once a run is processing 200 accounts, a plain log
line like "enrichment failed" is useless - failed for *which* account, in
*which* run? The obvious fix is passing run_id/account_id into every
logging call by hand, but that's easy to forget and clutters every call
site. Instead we use a `contextvar` - a piece of Python's standard library
that lets a value be set once for the current logical "thread of
execution" and read back automatically anywhere further down the call
stack, without passing it as an explicit argument. We set run_id (and
optionally account_id/contact_id) once via `log_context(...)` at the top of
a pipeline stage, and every log line emitted underneath it picks the values
up automatically.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

_run_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "run_id", default=None
)
_account_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "account_id", default=None
)
_contact_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "contact_id", default=None
)


class JsonFormatter(logging.Formatter):
    """Renders each log record as one JSON object per line, with the
    current run_id/account_id/contact_id (if any) folded in automatically.

    Deliberately does NOT include arbitrary extra kwargs that might contain
    PII (e.g. a raw contact email) - only the fixed set of fields below is
    ever emitted. Anything a caller wants logged must go through `message`
    as a human-written string, which keeps PII redaction a matter of
    reviewing call sites (see tests/test_logging.py) rather than trusting
    every future caller to remember not to pass raw contact data as a
    structured field.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = _run_id_var.get()
        if run_id:
            payload["run_id"] = run_id
        account_id = _account_id_var.get()
        if account_id:
            payload["account_id"] = account_id
        contact_id = _contact_id_var.get()
        if contact_id:
            payload["contact_id"] = contact_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


@contextlib.contextmanager
def log_context(
    run_id: Optional[str] = None,
    account_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> Iterator[None]:
    """Bind correlation IDs for the duration of the `with` block. Nested
    contexts compose - entering an account_id context inside an existing
    run_id context keeps the run_id and adds the account_id, then restores
    the outer state on exit.

    Also mirrors the current run_id/account_id/contact_id onto Sentry's
    scope as tags (Step 22) - a no-op when Sentry isn't configured
    (infra.observability.set_correlation_tags checks is_active() itself),
    so a Sentry event and the JSON log line next to it always agree on
    which run/account/contact they came from."""

    tokens = []
    if run_id is not None:
        tokens.append((_run_id_var, _run_id_var.set(run_id)))
    if account_id is not None:
        tokens.append((_account_id_var, _account_id_var.set(account_id)))
    if contact_id is not None:
        tokens.append((_contact_id_var, _contact_id_var.set(contact_id)))
    _sync_sentry_tags()
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
        _sync_sentry_tags()


def _sync_sentry_tags() -> None:
    from infra.observability import set_correlation_tags

    set_correlation_tags(
        run_id=_run_id_var.get(), account_id=_account_id_var.get(), contact_id=_contact_id_var.get()
    )
