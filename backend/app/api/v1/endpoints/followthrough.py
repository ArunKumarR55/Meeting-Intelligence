"""Read-only follow-through APIs for drift observations and reminder health."""

from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.db.models.domain import ActionItem, Meeting, MeetingRiskStatus, ReminderDelivery, TaskStatus, utc_now
from app.services.workflows.action_item_drift import list_action_item_drift


def _today() -> date:
    try:
        return datetime.now(ZoneInfo(settings.reminder_timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc).date()


router = APIRouter()


@router.get("/drift", summary="List cross-meeting action-item observations")
def get_action_item_drift(db: Session = Depends(get_db)) -> dict[str, object]:
    """Return the latest persisted follow-up signals and explain missing mentions."""
    records = list_action_item_drift(db)
    return {
        "items": records,
        "count": len(records),
        "interpretation": "Not mentioned means no matching task was extracted in that meeting; it does not prove the task was dropped.",
    }


@router.get("/reminders/status", summary="View reminder scheduler and outstanding items")
def get_reminder_status(db: Session = Depends(get_db)) -> dict[str, object]:
    """Expose counts and last successful delivery date without revealing addresses."""
    today = _today()
    overdue_count = db.scalar(
        select(func.count(ActionItem.id)).where(
            ActionItem.due_date.is_not(None),
            ActionItem.due_date < today,
            ActionItem.status != TaskStatus.COMPLETED,
        )
    ) or 0
    risk_count = 0
    risk_states = {
        (meeting_id, risk_index): state
        for meeting_id, risk_index, state in db.execute(
            select(MeetingRiskStatus.meeting_id, MeetingRiskStatus.risk_index, MeetingRiskStatus.status)
        ).all()
    }
    for meeting_id, risks in db.execute(select(Meeting.id, Meeting.risks)).all():
        if not isinstance(risks, list):
            continue
        for risk_index, risk in enumerate(risks):
            if risk_states.get((meeting_id, risk_index), "open") == "open" and isinstance(risk, dict) and str(risk.get("status") or risk.get("state") or "open").casefold() not in {
                "closed", "resolved", "complete", "completed", "mitigated"
            }:
                risk_count += 1
    last_delivery = db.scalar(select(func.max(ReminderDelivery.sent_at)))
    return {
        "enabled": settings.reminders_enabled,
        "interval_minutes": settings.reminders_interval_minutes,
        "timezone": settings.reminder_timezone,
        "overdue_task_count": int(overdue_count),
        "open_risk_count": risk_count,
        "last_delivery_at": last_delivery,
    }


@router.get("/tasks", summary="List open action items with calendar deadlines")
def get_followthrough_tasks(db: Session = Depends(get_db)) -> dict[str, object]:
    """Provide open, dated tasks for review and optional calendar synchronization."""
    rows = db.execute(
        select(ActionItem, Meeting)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(
            ActionItem.due_date.is_not(None),
            ActionItem.status != TaskStatus.COMPLETED,
        )
        .order_by(ActionItem.due_date.asc(), ActionItem.id.asc())
        .limit(500)
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "description": item.description,
                "owner": item.owner,
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "due_time": item.due_time.isoformat(timespec="minutes") if item.due_time else None,
                "status": item.status.value if hasattr(item.status, "value") else item.status,
                "meeting_id": meeting.id,
                "meeting_title": meeting.title,
            }
            for item, meeting in rows
        ]
    }


class RiskStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["open", "resolved"]


@router.get("/risks", summary="List extracted project risks and review state")
def get_followthrough_risks(db: Session = Depends(get_db)) -> dict[str, object]:
    states = {
        (meeting_id, risk_index): state
        for meeting_id, risk_index, state in db.execute(
            select(MeetingRiskStatus.meeting_id, MeetingRiskStatus.risk_index, MeetingRiskStatus.status)
        ).all()
    }
    meetings = db.scalars(select(Meeting).where(Meeting.risks != []).order_by(Meeting.created_at.desc())).all()
    items = []
    for meeting in meetings:
        for risk_index, risk in enumerate(meeting.risks or []):
            if not isinstance(risk, dict):
                continue
            provider_status = str(risk.get("status") or risk.get("state") or "open").casefold()
            state = states.get((meeting.id, risk_index), "open")
            if provider_status in {"closed", "resolved", "complete", "completed", "mitigated"}:
                state = "resolved"
            items.append({
                "meeting_id": meeting.id,
                "meeting_title": meeting.title,
                "risk_index": risk_index,
                "description": risk.get("description", "Unspecified project risk"),
                "severity": risk.get("severity", "Unspecified"),
                "mitigation": risk.get("mitigation"),
                "owner": risk.get("owner"),
                "status": state,
            })
    return {"items": items}


@router.patch("/risks/{meeting_id}/{risk_index}", summary="Mark a project risk open or resolved")
def set_risk_status(
    meeting_id: int,
    risk_index: int,
    request: RiskStatusRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if risk_index < 0 or risk_index >= len(meeting.risks or []):
        raise HTTPException(status_code=404, detail="Risk not found")
    risk = meeting.risks[risk_index]
    if not isinstance(risk, dict):
        raise HTTPException(status_code=404, detail="Risk not found")
    record = db.scalar(
        select(MeetingRiskStatus).where(
            MeetingRiskStatus.meeting_id == meeting_id,
            MeetingRiskStatus.risk_index == risk_index,
        )
    )
    if record is None:
        record = MeetingRiskStatus(
            meeting_id=meeting_id,
            risk_index=risk_index,
            status=request.status,
            updated_at=utc_now(),
        )
        db.add(record)
    else:
        record.status = request.status
        record.updated_at = utc_now()
    db.commit()
    return {"meeting_id": meeting_id, "risk_index": risk_index, "status": request.status}
