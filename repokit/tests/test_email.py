from email import message_from_bytes
from typing import ClassVar

import pytest
from repokit.email import send_email, send_email_from_env


class FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send_message", msg))


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def test_send_email_starttls_login_and_sends(fake_smtp):
    send_email(
        "<html>hi</html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    assert instance.host == "smtp.example.com"
    assert instance.port == 587
    assert instance.calls[0] == "starttls"
    assert instance.calls[1] == ("login", "user", "pass")
    assert instance.calls[2][0] == "send_message"


def test_send_email_message_has_plain_and_html_parts(fake_smtp):
    send_email(
        "<html><body>hi</body></html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    msg = instance.calls[2][1]
    parsed = message_from_bytes(msg.as_bytes())
    content_types = {part.get_content_type() for part in parsed.walk()}
    assert "text/plain" in content_types
    assert "text/html" in content_types


def test_send_email_sets_subject_and_addresses(fake_smtp):
    send_email(
        "<html>hi</html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    msg = instance.calls[2][1]
    assert msg["Subject"] == "PR digest"
    assert msg["From"] == "from@example.com"
    assert msg["To"] == "to@example.com"


def test_send_email_from_env_reads_smtp_settings_from_environment(
    fake_smtp, monkeypatch
):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")
    monkeypatch.setenv("DIGEST_FROM_EMAIL", "from@example.com")
    monkeypatch.setenv("DIGEST_TO_EMAIL", "to@example.com")

    send_email_from_env("<html>hi</html>", subject="PR digest")

    (instance,) = fake_smtp.instances
    assert instance.host == "smtp.example.com"
    assert instance.port == 587
    assert instance.calls[1] == ("login", "user", "pass")
    msg = instance.calls[2][1]
    assert msg["Subject"] == "PR digest"
    assert msg["From"] == "from@example.com"
    assert msg["To"] == "to@example.com"


def test_send_email_from_env_raises_on_missing_env_var(fake_smtp, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(KeyError):
        send_email_from_env("<html>hi</html>", subject="PR digest")
