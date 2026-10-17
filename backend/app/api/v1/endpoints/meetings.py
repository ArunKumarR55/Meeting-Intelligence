"""Meeting intelligence retrieval and speaker identity management endpoints."""

import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT
from app.core.database import get_db
from app.db.models.domain import ActionItem, Meeting, ReminderDelivery
from app.services.corporate_memory.rag_store import delete_meeting_transcript, index_meeting_transcript
from app.services.audio_ingestion.speaker_identity import infer_speaker_identities


router = APIRouter()
SUMMARY_PREVIEW_LENGTH = 240
logger = logging.getLogger(__name__)
MANAGED_UPLOAD_DIRECTORY = (PROJECT_ROOT / "data" / "uploads").resolve()


def _normalized_owner(value: object) -> str:
    """Normalize a complete owner label for safe, exact speaker-label matching."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _speaker_owner_aliases(meeting: Meeting, speaker_id: str) -> set[str]:
    """Return only explicit aliases for a diarized speaker, not fuzzy name matches."""
    aliases = {speaker_id, speaker_id.replace("_", " ")}
    match = re.fullmatch(r"speaker[_\s-]*(\d+)", speaker_id, flags=re.IGNORECASE)
    if match:
        number = int(match.group(1))
        aliases.update({f"Speaker {number}", f"Speaker {number:02d}"})

    for segment in meeting.transcript_segments or []:
        if not isinstance(segment, dict) or segment.get("speaker_id") != speaker_id:
            continue
        text = segment.get("text")
        if isinstance(text, str):
            prefix = re.match(r"^\s*(Speaker\s+[^:]{1,64})\s*:", text, flags=re.IGNORECASE)
            if prefix:
                aliases.add(prefix.group(1).strip())

    current_identity = (meeting.speaker_identities or {}).get(speaker_id)
    if isinstance(current_identity, dict) and isinstance(current_identity.get("name"), str):
        aliases.add(current_identity["name"])
    return {_normalized_owner(alias) for alias in aliases if _normalized_owner(alias)}


def _sync_speaker_owner_labels(meeting: Meeting, db: Session, speaker_id: str, new_label: str) -> dict[str, int]:
    """Replace exact speaker aliases in stored task and risk owner fields."""
    aliases = _speaker_owner_aliases(meeting, speaker_id)
    replacement = new_label.strip()
    tasks_updated = 0
    for task in db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting.id)).all():
        if task.owner and _normalized_owner(task.owner) in aliases and task.owner != replacement:
            task.owner = replacement
            tasks_updated += 1

    updated_risks = []
    risks_updated = 0
    for risk in meeting.risks or []:
        if not isinstance(risk, dict):
            updated_risks.append(risk)
            continue
        updated_risk = dict(risk)
        owner = updated_risk.get("owner")
        if owner and _normalized_owner(owner) in aliases and owner != replacement:
            updated_risk["owner"] = replacement
            risks_updated += 1
        updated_risks.append(updated_risk)
    if risks_updated:
        meeting.risks = updated_risks
    return {"action_items_updated": tasks_updated, "risks_updated": risks_updated}


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
                "jira_issue_url": item.jira_issue_url,
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


@router.delete("/{meeting_id}", status_code=status.HTTP_200_OK, summary="Delete a meeting and its local data")
def delete_meeting(meeting_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Delete a meeting, its transcript index, and its managed uploaded recording."""
    meeting = db.scalar(
        select(Meeting)
        .options(selectinload(Meeting.action_items))
        .where(Meeting.id == meeting_id)
    )
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    transcript = meeting.transcript
    transcript_segments = list(meeting.transcript_segments or [])
    action_item_ids = [item.id for item in meeting.action_items]
    recording_path = Path(meeting.recording_path).expanduser() if meeting.recording_path else None
    managed_recording: Path | None = None
    recording_warning: str | None = None
    if recording_path is not None:
        try:
            resolved_recording = recording_path.resolve(strict=False)
            if resolved_recording.is_relative_to(MANAGED_UPLOAD_DIRECTORY):
                managed_recording = resolved_recording
            else:
                recording_warning = "The recording is outside AMIP's managed uploads folder and was left untouched."
        except OSError:
            recording_warning = "The recording path could not be verified and was left untouched."

    # Remove the vector data before committing the relational deletion. If this
    # operation fails, keep the meeting and its audio intact so deletion can be retried.
    try:
        delete_meeting_transcript(meeting_id)
    except Exception as error:
        logger.exception("Could not delete memory index for meeting %s", meeting_id)
        raise HTTPException(status_code=502, detail="Could not remove the meeting from memory; no meeting data was deleted") from error

    try:
        if action_item_ids:
            db.execute(
                delete(ReminderDelivery).where(
                    ReminderDelivery.reminder_type == "overdue_task",
                    ReminderDelivery.reference_key.in_([str(item_id) for item_id in action_item_ids]),
                )
            )
        db.execute(
            delete(ReminderDelivery).where(
                ReminderDelivery.reminder_type == "open_risk",
                ReminderDelivery.reference_key.like(f"{meeting_id}:%"),
            )
        )
        db.delete(meeting)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if transcript:
            try:
                index_meeting_transcript(meeting_id, transcript, transcript_segments)
            except Exception:
                logger.exception("Could not restore memory index after meeting deletion rollback for %s", meeting_id)
        logger.exception("Could not delete meeting %s from the database", meeting_id)
        raise HTTPException(status_code=500, detail="Meeting data could not be deleted; try again") from error

    recording_deleted = False
    if managed_recording is not None:
        try:
            managed_recording.unlink(missing_ok=True)
            recording_deleted = True
        except OSError:
            recording_warning = "The meeting was deleted, but its uploaded recording could not be removed from disk."
            logger.exception("Could not remove uploaded recording for deleted meeting %s", meeting_id)

    return {
        "meeting_id": meeting_id,
        "deleted": True,
        "recording_deleted": recording_deleted,
        "cleanup_warning": recording_warning,
        "external_items_note": "Previously created Jira or GitHub issues and calendar events remain in those services.",
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
    owner_updates = {"action_items_updated": 0, "risks_updated": 0}
    inferred = infer_speaker_identities(meeting.transcript_segments)
    for speaker_id, identity in inferred.items():
        existing_identity = identities.get(speaker_id)
        if not isinstance(existing_identity, dict) or not existing_identity.get("confirmed"):
            label = identity.get("name") if isinstance(identity, dict) else None
            if isinstance(label, str) and label.strip():
                updates = _sync_speaker_owner_labels(meeting, db, speaker_id, label.strip())
                for key, count in updates.items():
                    owner_updates[key] += count
            identities[speaker_id] = identity

    meeting.speaker_identities = identities
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not infer speaker identities for meeting %s", meeting_id)
        raise HTTPException(status_code=500, detail="Could not save inferred speaker names") from error
    return {"meeting_id": meeting.id, "speaker_identities": meeting.speaker_identities, **owner_updates}


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
    fallback_label = speaker_id.replace("_", " ")
    owner_label = request.name or fallback_label
    owner_updates = _sync_speaker_owner_labels(meeting, db, speaker_id, owner_label)
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
    return {"meeting_id": meeting.id, "speaker_identities": meeting.speaker_identities, **owner_updates}
