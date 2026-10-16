"""Meeting intelligence retrieval and speaker identity management endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.db.models.domain import Meeting
from app.services.audio_ingestion.speaker_identity import infer_speaker_identities


router = APIRouter()
SUMMARY_PREVIEW_LENGTH = 240
logger = logging.getLogger(__name__)


class SpeakerIdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str | None, Field(max_length=120)] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


@router.get("/", summary="List meetings")
def list_meetings(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Return compact meeting records, newest first."""
    meetings = db.scalars(select(Meeting).order_by(Meeting.created_at.desc(), Meeting.id.desc())).all()
    return [
        {
            "id": meeting.id,
            "title": meeting.title,
            "date_recorded": meeting.created_at,
            "summary_preview": (meeting.executive_summary or "")[:SUMMARY_PREVIEW_LENGTH],
        }
        for meeting in meetings
    ]


@router.get("/{meeting_id}", summary="Get complete meeting intelligence")
def get_meeting(meeting_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Return the redacted transcript, analytics, tasks, and decisions."""
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.action_items), selectinload(Meeting.decisions))
        .where(Meeting.id == meeting_id)
    )
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    return {
        "id": meeting.id,
        "title": meeting.title,
        "date_recorded": meeting.created_at,
        "transcript": meeting.transcript,
        "transcript_segments": meeting.transcript_segments,
        "speaker_identities": meeting.speaker_identities,
        "diarization_status": meeting.diarization_status,
        "executive_summary": meeting.executive_summary,
        "key_topics": meeting.key_topics,
        "timeline": meeting.timeline,
        "risks": meeting.risks,
        "behavioral_metrics": meeting.behavioral_metrics,
        "mom_recipients": meeting.mom_recipients,
        "mom_delivery_report": meeting.mom_delivery_report,
        "mom_sent_at": meeting.mom_sent_at,
        "action_items": [
            {
                "id": item.id,
                "description": item.description,
                "owner": item.owner,
                "due_date": item.due_date,
                "due_time": item.due_time,
                "status": item.status.value,
                "external_ticket_url": item.external_ticket_url,
            }
            for item in meeting.action_items
        ],
        "decisions": [
            {
                "id": decision.id,
                "statement": decision.statement,
                "state": decision.state.value,
                "rationale": decision.rationale,
                "timestamp_seconds": decision.timestamp_seconds,
            }
            for decision in meeting.decisions
        ],
    }


@router.post("/{meeting_id}/speakers/infer", summary="Infer speaker names from the transcript")
def infer_meeting_speaker_identities(
    meeting_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Infer high-confidence identities locally without changing transcript text."""
    meeting = db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    identities = dict(meeting.speaker_identities or {})
    inferred = infer_speaker_identities(meeting.transcript_segments)
    for speaker_id, identity in inferred.items():
        existing_identity = identities.get(speaker_id)
        if not isinstance(existing_identity, dict) or not existing_identity.get("confirmed"):
            identities[speaker_id] = identity

    meeting.speaker_identities = identities
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not infer speaker identities for meeting %s", meeting_id)
        raise HTTPException(status_code=500, detail="Could not save inferred speaker names") from error
    return {"meeting_id": meeting.id, "speaker_identities": meeting.speaker_identities}


@router.patch("/{meeting_id}/speakers/{speaker_id}", summary="Confirm or edit a speaker name")
def update_speaker_identity(
    meeting_id: int,
    speaker_id: str,
    request: SpeakerIdentityUpdate = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Persist a user-confirmed speaker name, or clear the inferred name."""
    meeting = db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    speaker_ids = {
        segment.get("speaker_id")
        for segment in meeting.transcript_segments
        if isinstance(segment.get("speaker_id"), str)
    }
    if speaker_id not in speaker_ids:
        raise HTTPException(status_code=404, detail="Speaker not found in this meeting")

    identities = dict(meeting.speaker_identities or {})
    if request.name is None:
        identities.pop(speaker_id, None)
    else:
        identities[speaker_id] = {
            "name": request.name,
            "confidence": 1.0,
            "source": "user_confirmed",
            "confirmed": True,
        }

    meeting.speaker_identities = identities
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save speaker identity for meeting %s", meeting_id)
        raise HTTPException(status_code=500, detail="Could not save speaker name") from error
    return {"meeting_id": meeting.id, "speaker_identities": meeting.speaker_identities}
