"""Operator notifications: ntfy push and email.

Channels are independent: each is enabled by its own config (see
MonitoringConfig), a failure on one never blocks the other, and callers
learn via the return value whether anything was delivered.
"""

import logging
import smtplib
from email.message import EmailMessage

import requests

from .config import get_config


def notify(title: str, body: str) -> bool:
    """Send to every configured channel. True if at least one delivered."""
    sent_push = notify_ntfy(title, body)
    sent_email = notify_email(title, body)
    return sent_push or sent_email


def notify_ntfy(title: str, body: str) -> bool:
    """Push via ntfy. True on delivery, False if unconfigured or failed."""
    config = get_config().monitoring
    if not config.ntfy_topic:
        logging.info("ntfy is not configured (no topic); skipping push.")
        return False
    try:
        resp = requests.post(
            f"{config.ntfy_url.rstrip('/')}/{config.ntfy_topic}",
            data=body.encode("utf-8"),
            headers={"Title": title},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logging.error(f"ntfy push failed: {e}")
        return False


def notify_email(subject: str, body: str) -> bool:
    """Send email notification."""
    config = get_config().monitoring
    if not config.smtp_host:
        logging.info("Email alerts are not configured (no SMTP host); skipping email.")
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_from or config.smtp_user or config.alert_email
    message["To"] = config.alert_email
    message.set_content(body)
    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            # A local relay (e.g. postfix on port 25) may not offer TLS.
            if smtp.has_extn("starttls"):
                smtp.starttls()
            if config.smtp_user:
                smtp.login(config.smtp_user, config.smtp_password.get_secret_value())
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logging.error(f"Alert email failed: {e}")
        return False
