import uuid

import pytest

from app.orm import AccountORM, ContactORM, ExternalCallAttemptORM, ProviderRecordORM, RunORM
from prospectforge.models.enums import CallStatus, ContactStatus, RunStatus
from prospectforge.privacy.erasure import ErasureError, erase_contact


def _contact(db_session, **overrides) -> ContactORM:
    account = AccountORM(id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co")
    db_session.add(account)
    db_session.flush()

    defaults = dict(
        id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales",
        seniority="vp", department="sales", email="jane@example.com", email_confidence="verified",
        linkedin_url="https://linkedin.com/in/janedoe", status=ContactStatus.ENRICHED,
    )
    defaults.update(overrides)
    contact = ContactORM(**defaults)
    db_session.add(contact)
    db_session.flush()
    return contact


def test_erase_contact_scrubs_personal_fields(db_session):
    contact = _contact(db_session)

    erased = erase_contact(contact.id, db_session)

    assert erased.name == "[erased]"
    assert erased.email is None
    assert erased.email_confidence is None
    assert erased.linkedin_url is None
    assert erased.status == ContactStatus.ERASED
    assert erased.erased_at is not None


def test_erase_contact_keeps_role_level_fields(db_session):
    """Title/seniority/department describe the role, not the person - see
    erasure.py's module docstring for why these are deliberately kept."""

    contact = _contact(db_session)

    erased = erase_contact(contact.id, db_session)

    assert erased.title == "VP of Sales"
    assert erased.seniority == "vp"
    assert erased.department == "sales"


def test_erase_contact_redacts_provider_record_payloads(db_session):
    contact = _contact(db_session)
    db_session.add(
        ProviderRecordORM(
            account_id=contact.account_id, contact_id=contact.id, provider="apollo",
            operation="contact_enrichment", payload={"email": "jane@example.com", "status": "verified"},
        )
    )
    db_session.flush()

    erase_contact(contact.id, db_session)

    record = db_session.query(ProviderRecordORM).filter_by(contact_id=contact.id).one()
    assert "jane@example.com" not in str(record.payload)
    assert record.payload["redacted"] is True


def test_erase_contact_redacts_external_call_attempt_error_messages(db_session):
    contact = _contact(db_session)
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    db_session.add(
        ExternalCallAttemptORM(
            run_id=run.id, contact_id=contact.id, provider="apollo", operation="contact_enrichment",
            attempt_number=1, status=CallStatus.FAILED_NON_RETRYABLE,
            error_message="HubSpot returned HTTP 400: invalid contact jane@example.com",
        )
    )
    db_session.flush()

    erase_contact(contact.id, db_session)

    attempt = db_session.query(ExternalCallAttemptORM).filter_by(contact_id=contact.id).one()
    assert "jane@example.com" not in attempt.error_message


def test_erase_contact_does_not_touch_unrelated_provider_records(db_session):
    contact = _contact(db_session)
    other_contact = _contact(db_session)
    db_session.add(
        ProviderRecordORM(
            account_id=other_contact.account_id, contact_id=other_contact.id, provider="apollo",
            operation="contact_enrichment", payload={"email": "other@example.com"},
        )
    )
    db_session.flush()

    erase_contact(contact.id, db_session)

    other_record = db_session.query(ProviderRecordORM).filter_by(contact_id=other_contact.id).one()
    assert other_record.payload == {"email": "other@example.com"}


def test_erasing_an_already_erased_contact_is_a_no_op(db_session):
    contact = _contact(db_session)
    first = erase_contact(contact.id, db_session)
    first_erased_at = first.erased_at

    second = erase_contact(contact.id, db_session)

    assert second.erased_at == first_erased_at  # not re-stamped


def test_erasing_an_unknown_contact_raises(db_session):
    with pytest.raises(ErasureError, match="No Contact found"):
        erase_contact(uuid.uuid4(), db_session)
