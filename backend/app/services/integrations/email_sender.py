"""SMTP delivery of meeting minutes with database-backed opt-out filtering."""

import html
import re
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Mapping
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    SMTP_FROM_EMAIL,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_SERVER,
    SMTP_USE_TLS,
    SMTP_USER,
    settings,
)
from app.db.models.domain import UnsubscribedEmail


_EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def _normalize_recipient(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise ValueError("Every recipient must be a valid email address")
    _, address = parseaddr(value.strip())
    address = address.strip()
    if not address or not _EMAIL_PATTERN.fullmatch(address):
        raise ValueError("Every recipient must be a valid email address")
    return address, address.casefold()


def _task_fields(task: object) -> tuple[str, str, str, str]:
    if isinstance(task, str):
        return task.strip(), "", "", ""

    if isinstance(task, Mapping):
        description = task.get("description") or task.get("title") or str(task)
        owner = task.get("owner")
        due_date = task.get("due_date") or task.get("deadline")
        status = task.get("status")
    else:
        description = getattr(task, "description", None) or str(task)
        owner = getattr(task, "owner", None)
        due_date = getattr(task, "due_date", None) or getattr(task, "deadline", None)
        status = getattr(task, "status", None)

    if hasattr(status, "value"):
        status = status.value
    return (
        str(description).strip(),
        str(owner).strip() if owner is not None else "",
        str(due_date).strip() if due_date is not None else "",
        str(status).strip() if status is not None else "",
    )


def _build_message(
    meeting_title: str,
    summary: str,
    task_rows: list[tuple[str, str, str, str]],
    recipient: str,
    sender: str,
) -> MIMEMultipart:
    title = meeting_title.replace("\r", " ").replace("\n", " ").strip()
    message = MIMEMultipart("alternative")
    message["Subject"] = str(Header(f"Meeting minutes: {title}", "utf-8"))
    message["From"] = sender
    message["To"] = recipient

    unsubscribe_url = (
        f"{settings.public_base_url}/api/v1/workflows/unsubscribe?"
        + urlencode({"email": recipient})
    )
    if settings.public_base_url.startswith("https://"):
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    plain_task_lines = []
    html_task_lines = []
    for description, owner, due_date, status in task_rows:
        details = [f"Owner: {owner}" if owner else None]
        details.append(f"Due: {due_date}" if due_date else None)
        details.append(f"Status: {status}" if status else None)
        detail_text = "; ".join(value for value in details if value)
        plain_task_lines.append(
            f"- {description}" + (f" ({detail_text})" if detail_text else "")
        )
        escaped_description = html.escape(description)
        if detail_text:
            escaped_description += f" <small>({html.escape(detail_text)})</small>"
        html_task_lines.append(f"<li>{escaped_description}</li>")

    task_plain = "\n".join(plain_task_lines) if plain_task_lines else "No action items."
    task_html = "\n".join(html_task_lines) if html_task_lines else "<li>No action items.</li>"
    plain_body = (
        f"Meeting: {title}\n\nSummary\n{summary.strip()}\n\n"
        f"Action items\n{task_plain}\n\n"
        f"Unsubscribe: {unsubscribe_url}\n"
    )
    html_body = (
        "<!doctype html><html><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<h2>Summary</h2><p>{html.escape(summary.strip()).replace(chr(10), '<br>')}</p>"
        f"<h2>Action items</h2><ul>{task_html}</ul>"
        "<hr><p style=\"color:#666;font-size:12px\">"
        f"To stop receiving meeting minutes, <a href=\"{html.escape(unsubscribe_url, quote=True)}\">unsubscribe</a>."
        "</p></body></html>"
    )
    message.attach(MIMEText(plain_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))
    return message


def send_mom_emails(
    meeting_title: str,
    summary: str,
    tasks: list,
    recipients: list,
    db_session: Session,
) -> dict[str, list[str]]:
    """Send personalized MOM messages after filtering all opt-out addresses."""
    if not isinstance(meeting_title, str) or not meeting_title.strip():
        raise ValueError("meeting_title must contain non-whitespace content")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must contain non-whitespace content")
    if not isinstance(tasks, list):
        raise TypeError("tasks must be a list")
    if not isinstance(recipients, list):
        raise TypeError("recipients must be a list")

    recipient_entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for recipient in recipients:
        address, normalized = _normalize_recipient(recipient)
        if normalized not in seen:
            recipient_entries.append((address, normalized))
            seen.add(normalized)

    if not recipient_entries:
        return {"sent": [], "unsubscribed": [], "failed": []}

    normalized_addresses = [normalized for _, normalized in recipient_entries]
    blocked_addresses = set(
        db_session.scalars(
            select(UnsubscribedEmail.email_normalized).where(
                UnsubscribedEmail.email_normalized.in_(normalized_addresses)
            )
        ).all()
    )
    allowed_recipients = [
        address
        for address, normalized in recipient_entries
        if normalized not in blocked_addresses
    ]
    unsubscribed_recipients = [
        address
        for address, normalized in recipient_entries
        if normalized in blocked_addresses
    ]
    if not allowed_recipients:
        return {
            "sent": [],
            "unsubscribed": unsubscribed_recipients,
            "failed": [],
        }

    if not SMTP_SERVER or not SMTP_USER or SMTP_PASS is None:
        raise RuntimeError("SMTP_SERVER, SMTP_USER, and SMTP_PASS must be configured")
    if SMTP_PORT < 1 or SMTP_PORT > 65_535:
        raise RuntimeError("SMTP_PORT must be between 1 and 65535")

    sender = SMTP_FROM_EMAIL or SMTP_USER
    _, sender_address = parseaddr(sender)
    if not sender_address or not _EMAIL_PATTERN.fullmatch(sender_address):
        raise RuntimeError("SMTP_FROM_EMAIL or SMTP_USER must be a valid sender address")
    sender = sender_address

    task_rows = [
        task_fields for task_fields in (_task_fields(task) for task in tasks)
        if task_fields[0]
    ]
    sent: list[str] = []
    failed: list[str] = []
    tls_context = ssl.create_default_context()

    if SMTP_PORT == 465:
        smtp_context = smtplib.SMTP_SSL(
            SMTP_SERVER,
            SMTP_PORT,
            timeout=30,
            context=tls_context,
        )
    else:
        smtp_context = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)

    with smtp_context as smtp:
        smtp.ehlo()
        if SMTP_PORT != 465 and SMTP_USE_TLS:
            smtp.starttls(context=tls_context)
            smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASS.get_secret_value())

        for recipient in allowed_recipients:
            message = _build_message(
                meeting_title=meeting_title,
                summary=summary,
                task_rows=task_rows,
                recipient=recipient,
                sender=sender,
            )
            try:
                refused = smtp.send_message(
                    message,
                    from_addr=sender_address,
                    to_addrs=[recipient],
                )
                if refused:
                    failed.append(recipient)
                else:
                    sent.append(recipient)
            except (OSError, smtplib.SMTPException):
                failed.append(recipient)

    return {
        "sent": sent,
        "unsubscribed": unsubscribed_recipients,
        "failed": failed,
    }


def send_followthrough_reminders(
    *,
    recipient: str,
    tasks: list[Mapping[str, object]],
    risks: list[Mapping[str, object]],
) -> None:
    """Send one escaped HTML reminder digest to one already-authorized recipient."""
    address, _ = _normalize_recipient(recipient)
    if not SMTP_SERVER or not SMTP_USER or SMTP_PASS is None:
        raise RuntimeError("SMTP_SERVER, SMTP_USER, and SMTP_PASS must be configured")
    sender = SMTP_FROM_EMAIL or SMTP_USER
    _, sender_address = parseaddr(sender)
    if not sender_address or not _EMAIL_PATTERN.fullmatch(sender_address):
        raise RuntimeError("SMTP_FROM_EMAIL or SMTP_USER must be a valid sender address")

    task_lines = []
    for task in tasks:
        title = html.escape(str(task.get("description", "Action item")))
        meeting = html.escape(str(task.get("meeting_title", "Meeting")))
        due_date = html.escape(str(task.get("due_date", "")))
        owner = html.escape(str(task.get("owner") or "Unassigned"))
        task_lines.append(
            f"<li><strong>{title}</strong> — {meeting}; due {due_date}; owner {owner}</li>"
        )

    risk_lines = []
    for risk in risks:
        description = html.escape(str(risk.get("description", "Open risk")))
        meeting = html.escape(str(risk.get("meeting_title", "Meeting")))
        severity = html.escape(str(risk.get("severity", "Unspecified")))
        risk_lines.append(f"<li><strong>{severity}:</strong> {description} — {meeting}</li>")

    content_sections = []
    if task_lines:
        content_sections.append(f"<h2>Overdue action items</h2><ul>{''.join(task_lines)}</ul>")
    if risk_lines:
        content_sections.append(f"<h2>Open risks</h2><ul>{''.join(risk_lines)}</ul>")
    unsubscribe_url = (
        f"{settings.public_base_url}/api/v1/workflows/unsubscribe?"
        + urlencode({"email": address})
    )
    message = MIMEMultipart("alternative")
    message["Subject"] = str(Header("AMIP follow-through reminders", "utf-8"))
    message["From"] = sender_address
    message["To"] = address
    plain_summary = [
        "Overdue action items:",
        *[f"- {item.get('description')} ({item.get('meeting_title')}, due {item.get('due_date')})" for item in tasks],
        "",
        "Open risks:",
        *[f"- {item.get('severity')}: {item.get('description')} ({item.get('meeting_title')})" for item in risks],
        "",
        f"Unsubscribe: {unsubscribe_url}",
    ]
    html_body = (
        "<!doctype html><html><body style=\"font:15px/1.55 Arial,sans-serif;color:#172033\">"
        "<h1>Follow-through reminders</h1>"
        + "".join(content_sections)
        + f"<hr><p style=\"color:#65728a;font-size:12px\">To stop receiving automated meeting emails, <a href=\"{html.escape(unsubscribe_url, quote=True)}\">unsubscribe</a>.</p></body></html>"
    )
    message.attach(MIMEText("\n".join(plain_summary), "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    tls_context = ssl.create_default_context()
    if SMTP_PORT == 465:
        smtp_context = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30, context=tls_context)
    else:
        smtp_context = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    with smtp_context as smtp:
        smtp.ehlo()
        if SMTP_PORT != 465 and SMTP_USE_TLS:
            smtp.starttls(context=tls_context)
            smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASS.get_secret_value())
        refused = smtp.send_message(message, from_addr=sender_address, to_addrs=[address])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
