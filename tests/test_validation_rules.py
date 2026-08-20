import uuid

from prospectforge.models import Account, Contact
from prospectforge.validation.rules import (
    is_plausible_email,
    is_plausible_url,
    validate_account,
    validate_contact,
)


def test_plausible_email_accepted():
    assert is_plausible_email("jane.doe@example.com") is True


def test_implausible_emails_rejected():
    assert is_plausible_email("not-an-email") is False
    assert is_plausible_email("missing-domain@") is False
    assert is_plausible_email("") is False
    assert is_plausible_email(None) is False  # type: ignore[arg-type]


def test_plausible_url_accepted():
    assert is_plausible_url("https://linkedin.com/in/janedoe") is True


def test_implausible_urls_rejected():
    assert is_plausible_url("not a url") is False
    assert is_plausible_url("linkedin.com/in/janedoe") is False  # missing scheme


def test_validate_contact_flags_bad_email():
    contact = Contact(id=uuid.uuid4(), account_id=uuid.uuid4(), name="X", email="not-an-email")
    issues = validate_contact(contact)
    assert any("email" in issue for issue in issues)


def test_validate_contact_flags_bad_linkedin_url():
    contact = Contact(
        id=uuid.uuid4(), account_id=uuid.uuid4(), name="X", linkedin_url="not a url"
    )
    issues = validate_contact(contact)
    assert any("linkedin_url" in issue for issue in issues)


def test_validate_contact_clean_data_has_no_issues():
    contact = Contact(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        name="X",
        email="jane@example.com",
        linkedin_url="https://linkedin.com/in/jane",
    )
    assert validate_contact(contact) == []


def test_validate_account_flags_negative_employee_count():
    account = Account(id=uuid.uuid4(), domain="example.com", name="X", employee_count=-5)
    issues = validate_account(account)
    assert any("employee_count" in issue for issue in issues)


def test_validate_account_clean_data_has_no_issues():
    account = Account(id=uuid.uuid4(), domain="example.com", name="X", employee_count=100)
    assert validate_account(account) == []
