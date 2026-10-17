"""Follow-through APIs for action-item management, drift review, and reminders."""

from datetime import date, datetime, time, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import get_db
from app.db.models.domain import ActionItem, ActionItemDrift, CalendarTaskSync, Meeting, MeetingRiskStatus, ReminderDelivery, TaskStatus, utc_now
from app.services.integrations.calendar_service import CalendarConfigurationError, CalendarProviderError, push_action_item_deadline
from app.services.workflows.jira_sync import update_jira_issue_deadline
from app.services.workflows.ticket_sync import update_github_issue_deadline
from app.services.workflows.action_item_drift import list_action_item_drift


def _today() -> date:
    try:
        return datetime.now(ZoneInfo(settings.reminder_timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc).date()


router = APIRouter()


class ActionItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: date | None = None
    due_time: time | None = None
    status: TaskStatus | None = None


class DriftReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "reviewed", "carried_forward", "resolved"]


@router.get("/drift", summary="List cross-meeting action-item observations")
def get_action_item_drift(db: Session = Depends(get_db)) -> dict[str, object]:
    """Return the latest persisted follow-up signals and explain missing mentions."""
    records = list_action_item_drift(db)
    return {
        "items": records,
        "count": len(records),
        "interpretation": "Not mentioned means no matching task was extracted in that meeting; it does not prove the task was dropped.",
    }


@router.patch("/drift/{drift_id}", summary="Update a cross-meeting drift review state")
def update_drift_review(
    drift_id: int,
    request: DriftReviewRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    observation = db.get(ActionItemDrift, drift_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="Drift observation not found")
    observation.review_status = request.status
    db.commit()
    return {"id": observation.id, "review_status": observation.review_status}


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
    open_task_count = db.scalar(
        select(func.count(ActionItem.id)).where(ActionItem.status != TaskStatus.COMPLETED)
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
        "open_task_count": int(open_task_count),
        "open_risk_count": risk_count,
        "last_delivery_at": last_delivery,
    }


@router.get("/tasks", summary="List action items for follow-through")
def get_followthrough_tasks(db: Session = Depends(get_db)) -> dict[str, object]:
    """Provide open and completed tasks for follow-through and optional calendar sync."""
    rows = db.execute(
        select(ActionItem, Meeting)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .order_by(ActionItem.due_date.asc().nullslast(), ActionItem.id.asc())
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


@router.patch("/tasks/{action_item_id}", summary="Update an action item deadline or status")
def update_action_item(
    action_item_id: int,
    request: ActionItemUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item = db.get(ActionItem, action_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    supplied = request.model_fields_set
    if not supplied:
        raise HTTPException(status_code=422, detail="Provide a due date, due time, or task status to update")
    deadline_changed = "due_date" in supplied or "due_time" in supplied
    if "due_date" in supplied:
        if request.due_date is None:
            raise HTTPException(status_code=422, detail="A due date is required; clear the time separately if needed")
        item.due_date = request.due_date
    if "due_time" in supplied:
        if request.due_time is not None and item.due_date is None:
            raise HTTPException(status_code=422, detail="Set a due date before setting a due time")
        item.due_time = request.due_time
    if request.status is not None:
        item.status = request.status

    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not save the action item update") from error
    calendar_warnings: list[str] = []
    calendar_sync_results: list[dict[str, object]] = []
    issue_warnings: list[str] = []
    if deadline_changed and item.due_date is not None:
        if item.external_ticket_url:
            token = settings.github_token.get_secret_value() if settings.github_token else None
            if token:
                try:
                    update_github_issue_deadline(
                        item.external_ticket_url, token, item.due_date, item.due_time, item.description
                    )
                except (RuntimeError, TimeoutError, ValueError) as error:
                    issue_warnings.append(f"GitHub issue: {error}")
            else:
                issue_warnings.append("GitHub issue: configure GITHUB_TOKEN to update the linked issue deadline")
        if item.jira_issue_url:
            token = settings.jira_api_token.get_secret_value() if settings.jira_api_token else None
            if settings.jira_base_url and settings.jira_email and token:
                try:
                    update_jira_issue_deadline(
                        issue_url=item.jira_issue_url,
                        base_url=settings.jira_base_url,
                        email=settings.jira_email,
                        api_token=token,
                        due_date=item.due_date,
                    )
                except (RuntimeError, TimeoutError, ValueError) as error:
                    issue_warnings.append(f"Jira issue: {error}")
            else:
                issue_warnings.append("Jira issue: configure Jira credentials to update its due date")
        item = db.query(ActionItem).options(joinedload(ActionItem.meeting)).filter(ActionItem.id == item.id).one()
        synced_providers = db.scalars(
            select(CalendarTaskSync.provider).where(CalendarTaskSync.action_item_id == item.id)
        ).all()
        for provider in synced_providers:
            try:
                calendar_sync_results.append(push_action_item_deadline(provider, item, db))
            except (CalendarConfigurationError, CalendarProviderError, ValueError) as error:
                calendar_warnings.append(f"{provider}: {error}")

    return {
        "id": item.id,
        "meeting_id": item.meeting_id,
        "description": item.description,
        "owner": item.owner,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "due_time": item.due_time.isoformat(timespec="minutes") if item.due_time else None,
        "status": item.status.value if hasattr(item.status, "value") else item.status,
        "external_ticket_url": item.external_ticket_url,
        "jira_issue_url": item.jira_issue_url,
        "calendar_sync_warnings": calendar_warnings,
        "calendar_sync_results": calendar_sync_results,
        "sync_warnings": [*calendar_warnings, *issue_warnings],
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
