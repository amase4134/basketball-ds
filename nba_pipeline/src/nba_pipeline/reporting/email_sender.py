"""Email delivery for generated reports.

Credentials come from the environment or a gitignored `.env` beside the
project root. Gmail SMTP is the primary transport; when no app password is
configured the report is handed to the signed-in macOS Mail.app instead, so a
first run still delivers without any secret being stored.
"""

from __future__ import annotations

import logging
import os
import shutil
import smtplib
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from nba_pipeline.config import Settings, settings

logger = logging.getLogger(__name__)

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
# Gmail speaks implicit TLS on 465 and STARTTLS on 587.
STARTTLS_PORT = 587


class EmailNotConfigured(RuntimeError):
    """No transport could deliver the message."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str

    @property
    def uses_starttls(self) -> bool:
        return self.port == STARTTLS_PORT


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file, ignoring comments and blanks."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def environment(cfg: Settings | None = None) -> dict[str, str]:
    """Settings from `.env`, with real environment variables taking priority."""
    cfg = cfg or settings
    merged = load_dotenv(cfg.project_root / ".env")
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


def smtp_config(cfg: Settings | None = None) -> SmtpConfig | None:
    """Build an SMTP config, or None when no app password is available."""
    cfg = cfg or settings
    env = environment(cfg)
    password = env.get("SMTP_APP_PASSWORD", "").replace(" ", "")
    if not password:
        return None
    user = env.get("SMTP_USER") or cfg.report_recipient
    try:
        port = int(env.get("SMTP_PORT", DEFAULT_SMTP_PORT))
    except ValueError:
        logger.warning("SMTP_PORT is not a number; falling back to %s", DEFAULT_SMTP_PORT)
        port = DEFAULT_SMTP_PORT
    return SmtpConfig(
        host=env.get("SMTP_HOST", DEFAULT_SMTP_HOST),
        port=port,
        user=user,
        password=password,
        sender=env.get("SMTP_FROM") or user,
    )


def recipient(cfg: Settings | None = None, override: str | None = None) -> str:
    cfg = cfg or settings
    if override:
        return override
    return environment(cfg).get("NBA_REPORT_TO") or cfg.report_recipient


def build_message(
    *,
    subject: str,
    to: str,
    sender: str,
    text_body: str,
    html_body: str,
) -> EmailMessage:
    """A multipart/alternative message; clients pick HTML or plain text."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def send_via_smtp(message: EmailMessage, config: SmtpConfig) -> None:
    if config.uses_starttls:
        with smtplib.SMTP(config.host, config.port, timeout=60) as client:
            client.starttls()
            client.login(config.user, config.password)
            client.send_message(message)
        return
    with smtplib.SMTP_SSL(config.host, config.port, timeout=60) as client:
        client.login(config.user, config.password)
        client.send_message(message)


def send_via_mail_app(*, subject: str, to: str, text_body: str) -> None:
    """Hand the message to macOS Mail.app via AppleScript.

    Mail.app sends from the account already signed in on this Mac, so this path
    needs no credentials at all.
    """
    osascript = shutil.which("osascript")
    if not osascript:
        raise EmailNotConfigured(
            "osascript is unavailable; set SMTP_APP_PASSWORD in .env to send mail."
        )
    script = """
    on run argv
        set theSubject to item 1 of argv
        set theBody to item 2 of argv
        set theAddress to item 3 of argv
        tell application "Mail"
            set newMessage to make new outgoing message with properties ¬
                {subject:theSubject, content:theBody, visible:false}
            tell newMessage
                make new to recipient at end of to recipients ¬
                    with properties {address:theAddress}
                send
            end tell
        end tell
    end run
    """
    result = subprocess.run(
        [osascript, "-", subject, text_body, to],
        input=script,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise EmailNotConfigured(
            f"Mail.app delivery failed: {result.stderr.strip() or 'unknown error'}"
        )


def send_report(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    to: str | None = None,
    cfg: Settings | None = None,
    dry_run: bool = False,
) -> str:
    """Deliver a report and describe the transport that carried it."""
    cfg = cfg or settings
    address = recipient(cfg, to)
    if dry_run:
        logger.info("[dry-run] would email %s: %s", address, subject)
        return f"dry-run (no email sent to {address})"

    config = smtp_config(cfg)
    if config is not None:
        message = build_message(
            subject=subject,
            to=address,
            sender=config.sender,
            text_body=text_body,
            html_body=html_body,
        )
        try:
            send_via_smtp(message, config)
            return f"sent to {address} via {config.host}"
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("SMTP delivery failed (%s); trying Mail.app", exc)

    send_via_mail_app(subject=subject, to=address, text_body=text_body)
    return f"sent to {address} via Mail.app"
