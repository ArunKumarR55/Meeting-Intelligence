"""Meeting distribution, opt-out, and external task workflows."""

import html
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.db.models.domain import ActionItem, Meeting, MomEmailRecipient, UnsubscribedEmail
from app.services.integrations.email_sender import send_mom_emails
from app.services.workflows.ticket_sync import push_task_to_github
from app.services.workflows.jira_sync import create_jira_issue


logger = logging.getLogger(__name__)
router = APIRouter()
EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


class GitHubExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_item_id: int = Field(gt=0)
    repo_url: str = Field(min_length=1, max_length=2_048)
    token: str | None = Field(default=None, min_length=1)


class JiraExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_item_id: int = Field(gt=0)


class MeetingMinutesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recipients: list[str] = Field(min_length=1, max_length=500)


class MomRecipientCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "\r" in value or "\n" in value or not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("A valid email address is required")
        return value


@router.get("/recipients", summary="List saved meeting-minutes recipients")
def list_mom_recipients(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Return the local recipient directory in a stable display order."""
    records = db.scalars(
        select(MomEmailRecipient).order_by(
            MomEmailRecipient.display_name,
            MomEmailRecipient.email_normalized,
        )
    ).all()
    return [
        {
            "id": recipient.id,
            "email": recipient.email,
            "display_name": recipient.display_name,
            "created_at": recipient.created_at,
        }
        for recipient in records
    ]


@router.post("/recipients", status_code=status.HTTP_201_CREATED, summary="Save a MOM recipient")
def create_mom_recipient(
    request: MomRecipientCreateRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Add an email address to the local directory for future meeting uploads."""
    normalized_email = request.email.casefold()
    existing = db.scalar(
        select(MomEmailRecipient).where(
            MomEmailRecipient.email_normalized == normalized_email
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="This recipient is already saved")

    recipient = MomEmailRecipient(
        email=request.email,
        display_name=request.display_name,
    )
    db.add(recipient)
    try:
        db.commit()
        db.refresh(recipient)
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="This recipient is already saved") from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save MOM recipient")
        raise HTTPException(status_code=500, detail="Could not save recipient") from error
    return {
        "id": recipient.id,
        "email": recipient.email,
        "display_name": recipient.display_name,
        "created_at": recipient.created_at,
    }


@router.delete(
    "/recipients/{recipient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a saved MOM recipient",
)
def delete_mom_recipient(recipient_id: int, db: Session = Depends(get_db)) -> Response:
    """Remove an address from future recipient selections."""
    recipient = db.get(MomEmailRecipient, recipient_id)
    if recipient is None:
        raise HTTPException(status_code=404, detail="Recipient not found")
    db.delete(recipient)
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not delete MOM recipient %s", recipient_id)
        raise HTTPException(status_code=500, detail="Could not remove recipient") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _record_unsubscribe(email: str, db: Session) -> str:
    normalized_email = email.strip().casefold()
    if "\r" in email or "\n" in email or not EMAIL_PATTERN.fullmatch(email.strip()):
        raise ValueError("A valid email address is required")

    existing = db.scalar(
        select(UnsubscribedEmail).where(
            UnsubscribedEmail.email_normalized == normalized_email
        )
    )
    if existing is None:
        try:
            db.add(UnsubscribedEmail(email=email.strip()))
            db.commit()
        except IntegrityError:
            db.rollback()
            # A concurrent unsubscribe may have inserted the unique normalized address.
            if db.scalar(
                select(UnsubscribedEmail.id).where(
                    UnsubscribedEmail.email_normalized == normalized_email
                )
            ) is None:
                logger.warning("Could not save unsubscribe preference after a concurrent update")
                raise RuntimeError("Could not save unsubscribe preference")
        except SQLAlchemyError as error:
            db.rollback()
            logger.warning("Could not save unsubscribe preference (%s)", type(error).__name__)
            raise RuntimeError("Could not save unsubscribe preference") from error
    return email.strip()


@router.get("/unsubscribe", response_class=HTMLResponse, summary="Unsubscribe from MOM emails")
def unsubscribe(
    email: Annotated[str, Query(min_length=3, max_length=320)],
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Persist an email opt-out and return a safe HTML confirmation page."""
    try:
        normalized_email = _record_unsubscribe(email, db)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail="Could not save unsubscribe preference") from error

    escaped_email = html.escape(normalized_email)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email preferences updated</title>
<style>body{{margin:0;background:#f3f6fb;color:#172033;font:16px/1.5 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{max-width:520px;margin:24px;padding:36px;background:#fff;border:1px solid #dce3ee;border-radius:16px;box-shadow:0 12px 40px #17203312}}.mark{{color:#087f5b;font-size:28px}}h1{{margin:8px 0 12px;font-size:24px}}p{{color:#526078}}</style></head>
<body><main><div class="mark" aria-hidden="true">✓</div><h1>Email preferences updated</h1>
<p><strong>{escaped_email}</strong> has been unsubscribed from automated meeting minutes.</p>
<p>You will continue to receive other messages unless you change those preferences separately.</p></main></body></html>"""
    return HTMLResponse(content=page, status_code=200)


@router.post("/unsubscribe", status_code=204, summary="One-click unsubscribe from MOM emails")
def unsubscribe_one_click(
    email: Annotated[str, Query(min_length=3, max_length=320)],
    list_unsubscribe: Annotated[str, Form(alias="List-Unsubscribe")],
    db: Session = Depends(get_db),
) -> Response:
    """Handle RFC 8058 mail-client unsubscribe requests."""
    if list_unsubscribe.strip() != "One-Click":
        raise HTTPException(status_code=422, detail="Invalid one-click unsubscribe request")
    try:
        _record_unsubscribe(email, db)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail="Could not save unsubscribe preference") from error
    return Response(status_code=204)


@router.post("/export/github", summary="Create a GitHub issue for an action item")
def export_action_item_to_github(
    request: GitHubExportRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Create a GitHub issue and persist its external URL on the task."""
    action_item = db.get(ActionItem, request.action_item_id)
    if action_item is None:
        raise HTTPException(status_code=404, detail="Action item not found")

    configured_token = settings.github_token.get_secret_value() if settings.github_token else None
    token = request.token or configured_token
    if not token:
        raise HTTPException(status_code=503, detail="GitHub token is not configured")
    try:
        issue = push_task_to_github(
            action_item.description,
            request.repo_url,
            token,
            action_item.due_date,
            action_item.due_time,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="GitHub issue creation timed out") from error
    except RuntimeError as error:
        logger.warning("GitHub issue creation failed: %s", error)
        raise HTTPException(status_code=502, detail="GitHub issue creation failed") from error

    issue_url = issue["html_url"]
    action_item.external_ticket_url = issue_url
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("GitHub issue was created but its URL could not be stored")
        raise HTTPException(
            status_code=500,
            detail="Issue was created, but its URL could not be saved",
        ) from error
    return {
        "action_item_id": action_item.id,
        "external_ticket_url": issue_url,
        "github_issue_number": issue.get("number"),
    }


@router.post("/export/jira", summary="Create a Jira issue for an action item")
def export_action_item_to_jira(
    request: JiraExportRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Create a Jira issue using backend-only credentials and persist its URL."""
    action_item = db.get(ActionItem, request.action_item_id)
    if action_item is None:
        raise HTTPException(status_code=404, detail="Action item not found")
    if action_item.jira_issue_url:
        return {
            "action_item_id": action_item.id,
            "jira_issue_url": action_item.jira_issue_url,
            "already_synced": True,
        }

    token = settings.jira_api_token.get_secret_value() if settings.jira_api_token else None
    if not all((settings.jira_base_url, settings.jira_email, token, settings.jira_project_key)):
        raise HTTPException(
            status_code=503,
            detail="Jira is not configured. Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, and JIRA_PROJECT_KEY in backend/.env.",
        )

    try:
        issue = create_jira_issue(
            task_description=action_item.description,
            base_url=settings.jira_base_url or "",
            email=settings.jira_email or "",
            api_token=token or "",
            project_key=settings.jira_project_key or "",
            issue_type=settings.jira_issue_type,
            due_date=action_item.due_date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="Jira issue creation timed out") from error
    except RuntimeError as error:
        logger.warning("Jira issue creation failed for action item %s: %s", action_item.id, error)
        raise HTTPException(status_code=502, detail="Jira issue creation failed. Check the Jira project and token permissions.") from error

    action_item.jira_issue_url = issue["issue_url"]
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Jira issue was created but its URL could not be stored")
        raise HTTPException(
            status_code=500,
            detail="Jira issue was created, but its URL could not be saved",
        ) from error
    return {
        "action_item_id": action_item.id,
        "jira_issue_key": issue["issue_key"],
        "jira_issue_url": issue["issue_url"],
        "already_synced": False,
    }


@router.post("/send-mom/{meeting_id}", summary="Email meeting minutes")
def send_meeting_minutes(
    meeting_id: int,
    request: MeetingMinutesRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Send the meeting summary and action items to requested recipients."""
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.action_items))
        .where(Meeting.id == meeting_id)
    )
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.executive_summary:
        raise HTTPException(status_code=422, detail="Meeting has no executive summary")

    try:
        delivery_report = send_mom_emails(
            meeting_title=meeting.title,
            summary=meeting.executive_summary,
            tasks=list(meeting.action_items),
            recipients=request.recipients,
            db_session=db,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        logger.error("MOM email delivery is not configured: %s", error)
        raise HTTPException(status_code=503, detail="MOM email delivery is not configured") from error
    except Exception as error:
        logger.exception("MOM email delivery failed for meeting %s", meeting_id)
        raise HTTPException(status_code=502, detail="Could not send meeting minutes") from error
    return {"meeting_id": meeting.id, **delivery_report}
