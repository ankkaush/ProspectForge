import io
import json
import logging

from app.logging import JsonFormatter, log_context


def _capture_one_log_line(fn) -> dict:
    logger = logging.getLogger("prospectforge.test")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    fn(logger)

    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


def test_log_line_includes_run_id_when_in_context():
    def emit(logger):
        with log_context(run_id="11111111-1111-1111-1111-111111111111"):
            logger.info("enrichment failed")

    record = _capture_one_log_line(emit)
    assert record["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert record["message"] == "enrichment failed"


def test_log_line_includes_account_id_nested_inside_run_context():
    def emit(logger):
        with log_context(run_id="run-1"):
            with log_context(account_id="account-42"):
                logger.info("account processed")

    record = _capture_one_log_line(emit)
    assert record["run_id"] == "run-1"
    assert record["account_id"] == "account-42"


def test_context_is_cleared_after_the_with_block_exits():
    def emit(logger):
        with log_context(run_id="run-1"):
            pass
        logger.info("outside any run context")

    record = _capture_one_log_line(emit)
    assert "run_id" not in record


def test_formatter_never_emits_fields_outside_the_fixed_allowlist():
    """The formatter only ever reads message/level/logger/timestamp plus
    the three correlation IDs - there's no code path for a caller to smuggle
    an arbitrary structured field (like a raw contact email) into a log
    line, which is what makes 'no PII in logs' a property of the logging
    module itself rather than a rule every future call site has to
    remember."""

    def emit(logger):
        # Even if a caller tries to pass extra structured data, the
        # formatter's fixed field list means it never reaches the output.
        logger.info("contact enriched", extra={"contact_email": "jane@example.com"})

    record = _capture_one_log_line(emit)
    assert set(record.keys()) <= {
        "timestamp",
        "level",
        "logger",
        "message",
        "run_id",
        "account_id",
        "contact_id",
        "exception",
    }
    assert "jane@example.com" not in json.dumps(record)
