"""Send digest email via SMTP."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from research_trend_bot.models import AppConfig

logger = logging.getLogger(__name__)


def send_email(
    config: AppConfig,
    smtp_password: str,
    subject: str,
    html_body: str,
    plain_body: str,
) -> None:
    """Send an email with HTML and plain-text parts via SMTP/TLS."""
    email_cfg = config.email

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{email_cfg.sender_name} <{email_cfg.sender_address}>"
    msg["To"] = ", ".join(email_cfg.recipients)
    msg["Subject"] = subject

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    logger.info(
        "Sending email to %s via %s:%d",
        email_cfg.recipients,
        email_cfg.smtp_host,
        email_cfg.smtp_port,
    )

    with smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port) as server:
        server.starttls()
        server.login(email_cfg.sender_address, smtp_password)
        server.sendmail(
            email_cfg.sender_address,
            email_cfg.recipients,
            msg.as_string(),
        )

    logger.info("Email sent successfully")
