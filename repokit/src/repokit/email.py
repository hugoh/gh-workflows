"""Sends a rendered HTML report via an SMTP relay.

Kept separate from repo-fetching concerns so a consumer can swap this for a
Slack post, a workflow artifact, or nothing at all, without touching how it
fetches or renders a report.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(
    html_body: str,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText("This email requires an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


def send_email_from_env(html_body: str, *, subject: str) -> None:
    """send_email(), reading the relay and recipient from the environment:
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, DIGEST_FROM_EMAIL,
    DIGEST_TO_EMAIL.
    """
    send_email(
        html_body,
        smtp_host=os.environ["SMTP_HOST"],
        smtp_port=int(os.environ["SMTP_PORT"]),
        smtp_user=os.environ["SMTP_USERNAME"],
        smtp_password=os.environ["SMTP_PASSWORD"],
        from_addr=os.environ["DIGEST_FROM_EMAIL"],
        to_addr=os.environ["DIGEST_TO_EMAIL"],
        subject=subject,
    )
