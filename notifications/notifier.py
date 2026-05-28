"""
notifier.py — Email + Telegram delivery for swing signal reports
─────────────────────────────────────────────────────────────────
Reads credentials from .env:
  EMAIL_SENDER        — Gmail address to send from
  EMAIL_PASSWORD      — Gmail app password
  EMAIL_RECIPIENT     — Your email address
  TELEGRAM_BOT_TOKEN  — Telegram bot token
  TELEGRAM_CHAT_ID    — Your Telegram chat ID
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

logger = logging.getLogger(__name__)


def send_email(subject: str, html_body: str, text_body: str = ""):
    """Send HTML email via Gmail SMTP."""
    sender    = os.getenv("EMAIL_SENDER", "")
    password  = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "")

    if not all([sender, password, recipient]):
        logger.warning("Email not configured — skipping (set EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT in .env)")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = recipient

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())

        logger.info(f"Email sent: {subject}")

    except Exception as e:
        logger.error(f"Email send failed: {e}")


def send_telegram(message: str):
    """Send plain-text message via Telegram bot."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not all([token, chat_id]):
        logger.warning("Telegram not configured — skipping (set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in .env)")
        return

    # Telegram has a 4096 char limit — split if needed
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": ""},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"Telegram send failed: {resp.text}")
            else:
                logger.info("Telegram message sent")
        except Exception as e:
            logger.error(f"Telegram error: {e}")


def send_discord(message: str) -> bool:
    try:
        from notifications.discord import send_discord_message
        success = send_discord_message(message)
        if success:
            logger.info("Discord message sent")
        else:
            logger.warning("Discord send failed (invalid webhook or network issue)")
        return success
    except Exception as e:
        logger.warning(f"Discord send error: {e}")
        return False


def deliver_report(
    subject: str,
    html_report: str,
    text_report: str,
):
    """Send report via all configured channels: email, Telegram, Discord."""
    send_email(subject, html_report, text_report)
    send_telegram(text_report)
    # Discord: strip HTML tags, trim to 1900 chars
    import re
    _plain = re.sub(r"<[^>]+>", "", text_report)
    _plain = re.sub(r"\n{3,}", "\n\n", _plain).strip()
    _discord_msg = f"**{subject}**\n\n{_plain}"[:1900]
    send_discord(_discord_msg)