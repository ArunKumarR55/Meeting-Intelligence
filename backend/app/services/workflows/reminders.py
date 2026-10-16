"""Daily, idempotent email digests for overdue tasks and active meeting risks."""

import logging
import hashlib
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import SMTP_USER, settings
from app.core.database import SessionLocal
from app.db.models.domain import (
    ActionItem,
    Meeting,
    MeetingRiskStatus,
    ReminderDelivery,
    TaskStatus,
    UnsubscribedEmail,
    utc_now,
)
from app.services.integrations.email_sender import send_followthrough_reminders


logger = logging.getLogger(__name__)


def _local_today() -> date:
    try:
        return datetime.now(ZoneInfo(settings.reminder_timezone)).date()
    except ZoneInfoNotFoundError as error:
        logger.error("Invalid REMINDER_TIMEZONE configuration; using UTC")
        return datetime.now(timezone.utc).date()


def _is_open_risk(risk: object) -> bool:
    if not isinstance(risk, dict):
        return False
    state = str(risk.get("status") or risk.get("state") or "open").strip().casefold()
    return state not in {"closed", "resolved", "complete", "completed", "mitigated"}


def _meeting_recipients(meeting: Meeting) -> list[str]:
    recipients = [value.strip() for value in (meeting.mom_recipients or []) if isinstance(value, str) and value.strip()]
    if not recipients and SMTP_USER:
        recipients = [SMTP_USER.strip()]
    unique: dict[str, str] = {}
    for recipient in recipients:
        unique.setdefault(recipient.casefold(), recipient)
    return list(unique.values())


def send_due_reminders(db: Session | None = None) -> dict[str, int]:
    """Send at most one reminder per matching item, recipient, and local calendar date."""
    owns_session = db is None
    session = db or SessionLocal()
    reminder_day = _local_today()
    sent_digests = 0
    failed_digests = 0
    try:
        overdue_items = list(
            session.scalars(
                select(ActionItem)
                .options(joinedload(ActionItem.meeting))
                .where(
                    ActionItem.due_date.is_not(None),
                    ActionItem.due_date < reminder_day,
                    ActionItem.status != TaskStatus.COMPLETED,
                )
                .order_by(ActionItem.due_date, ActionItem.id)
            ).all()
        )
        meetings_with_risks = list(
            session.scalars(
                select(Meeting).where(Meeting.risks != [])
            ).all()
        )
        risk_states = {
            (meeting_id, risk_index): state
            for meeting_id, risk_index, state in session.execute(
                select(MeetingRiskStatus.meeting_id, MeetingRiskStatus.risk_index, MeetingRiskStatus.status)
            ).all()
        }

        alerts_by_recipient: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
            lambda: {"tasks": [], "risks": []}
        )
        refs_by_recipient: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

        for item in overdue_items:
            for recipient in _meeting_recipients(item.meeting):
                normalized = recipient.casefold()
                if session.scalar(
                    select(ReminderDelivery.id).where(
                        ReminderDelivery.reminder_type == "overdue_task",
                        ReminderDelivery.reference_key == str(item.id),
                        ReminderDelivery.reminder_date == reminder_day,
                        ReminderDelivery.recipient_normalized == normalized,
                    )
                ):
                    continue
                alerts_by_recipient[normalized]["tasks"].append({
                    "description": item.description,
                    "owner": item.owner,
                    "due_date": item.due_date.isoformat() if item.due_date else "",
                    "meeting_title": item.meeting.title,
                })
                refs_by_recipient[normalized].append(("overdue_task", str(item.id), recipient))

        for meeting in meetings_with_risks:
            for risk_index, risk in enumerate(meeting.risks or []):
                status = risk_states.get((meeting.id, risk_index), "open")
                if status != "open" or not _is_open_risk(risk):
                    continue
                reference_key = f"{meeting.id}:{risk_index}"
                for recipient in _meeting_recipients(meeting):
                    normalized = recipient.casefold()
                    if session.scalar(
                        select(ReminderDelivery.id).where(
                            ReminderDelivery.reminder_type == "open_risk",
                            ReminderDelivery.reference_key == reference_key,
                            ReminderDelivery.reminder_date == reminder_day,
                            ReminderDelivery.recipient_normalized == normalized,
                        )
                    ):
                        continue
                    risk_data = risk if isinstance(risk, dict) else {}
                    alerts_by_recipient[normalized]["risks"].append({
                        "description": risk_data.get("description", "Unspecified meeting risk"),
                        "severity": risk_data.get("severity", "Unspecified"),
                        "meeting_title": meeting.title,
                    })
                    refs_by_recipient[normalized].append(("open_risk", reference_key, recipient))

        unsubscribed = set(session.scalars(select(UnsubscribedEmail.email_normalized)).all())
        for normalized, alerts in alerts_by_recipient.items():
            if normalized in unsubscribed or not (alerts["tasks"] or alerts["risks"]):
                continue
            recipient = refs_by_recipient[normalized][0][2]
            try:
                send_followthrough_reminders(
                    recipient=recipient,
                    tasks=alerts["tasks"],
                    risks=alerts["risks"],
                )
                session.add_all([
                    ReminderDelivery(
                        reminder_type=reminder_type,
                        reference_key=reference_key,
                        reminder_date=reminder_day,
                        recipient=address,
                        recipient_normalized=normalized,
                        sent_at=utc_now(),
                    )
                    for reminder_type, reference_key, address in refs_by_recipient[normalized]
                ])
                session.commit()
                sent_digests += 1
            except Exception:
                session.rollback()
                failed_digests += 1
                recipient_ref = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
                logger.exception("Could not send follow-through reminder digest (recipient_ref=%s)", recipient_ref)
        return {"sent_digests": sent_digests, "failed_digests": failed_digests}
    finally:
        if owns_session:
            session.close()
