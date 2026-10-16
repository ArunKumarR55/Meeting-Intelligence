"""Audio upload and local intelligence processing endpoint."""

import logging
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.database import get_db
from app.db.models.domain import ActionItem, Decision, Meeting, utc_now
from app.db.models.domain import DecisionState, TaskStatus
from app.services.analytics.extractor import (
    LLMProviderUnavailableError,
    LLMStructuredOutputError,
    extract_meeting_intelligence,
)
from app.services.audio_ingestion.pipeline import process_audio_with_diarization
from app.services.audio_ingestion.speaker_identity import infer_speaker_identities
from app.services.behavioral_analytics.metrics import calculate_participation_metrics
from app.services.corporate_memory.rag_store import (
    delete_meeting_transcript,
    index_meeting_transcript,
)
from app.services.integrations.email_sender import send_mom_emails
from app.services.workflows.action_item_drift import track_action_item_drift


logger = logging.getLogger(__name__)
router = APIRouter()
UPLOAD_DIRECTORY = PROJECT_ROOT / "data" / "uploads"
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_AUDIO_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".ogg", ".wav", ".webm",
}
UPLOAD_CHUNK_BYTES = 1024 * 1024
EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def _parse_recipients(raw_recipients: str) -> list[str]:
    """Parse and normalize the optional JSON recipient list from the upload form."""
    try:
        values = json.loads(raw_recipients)
    except (TypeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="recipients must be a JSON array") from error
    if not isinstance(values, list) or len(values) > 500:
        raise HTTPException(status_code=422, detail="recipients must be an array of at most 500 emails")

    recipients: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail="Every recipient must be an email address")
        email = value.strip()
        normalized = email.casefold()
        if "\r" in email or "\n" in email or not EMAIL_PATTERN.fullmatch(email):
            raise HTTPException(status_code=422, detail="Every recipient must be a valid email address")
        if normalized not in seen:
            recipients.append(email)
            seen.add(normalized)
    return recipients


def _save_upload(upload: UploadFile) -> Path:
    """Stream an allowed audio container to a generated local filename."""
    original_name = upload.filename or ""
    extension = Path(original_name).suffix.casefold()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported audio file type",
        )

    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIRECTORY / f"{uuid4().hex}{extension}"
    total_bytes = 0
    try:
        with destination.open("xb") as output_file:
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Audio upload exceeds the 500 MiB limit",
                    )
                output_file.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        logger.exception("Could not save uploaded audio")
        raise HTTPException(status_code=500, detail="Could not save uploaded audio") from error

    if total_bytes == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty")
    return destination


@router.post("/upload", status_code=status.HTTP_201_CREATED, summary="Process uploaded audio")
def upload_audio(
    file: UploadFile = File(...),
    title: str = Form(...),
    recipients: str = Form("[]"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Transcribe, redact, extract, persist, and index an uploaded recording."""
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 500:
        raise HTTPException(
            status_code=400,
            detail="title must contain between 1 and 500 characters",
        )
    selected_recipients = _parse_recipients(recipients)

    audio_path = _save_upload(file)
    meeting_reference_date = datetime.now(ZoneInfo(settings.reminder_timezone)).date()
    indexed_meeting_id: int | None = None
    failure_stage = "local audio transcription"
    try:
        audio_result = process_audio_with_diarization(audio_path)
        transcript = audio_result.transcript
        if not transcript.strip():
            raise HTTPException(
                status_code=422,
                detail="No speech could be transcribed from the uploaded audio",
            )
        failure_stage = "Gemini intelligence extraction"
        intelligence = extract_meeting_intelligence(transcript, meeting_date=meeting_reference_date)
        failure_stage = "behavioral analytics calculation"
        metrics = calculate_participation_metrics(transcript)
        transcript_segments = [
            segment.model_dump(mode="json")
            for segment in audio_result.transcript_segments
        ]
        speaker_identities = infer_speaker_identities(transcript_segments)

        failure_stage = "meeting database and memory storage"
        meeting = Meeting(
            title=normalized_title,
            recording_path=str(audio_path.resolve()),
            transcript=transcript,
            transcript_segments=transcript_segments,
            speaker_identities=speaker_identities,
            diarization_status=audio_result.diarization_status,
            executive_summary=intelligence.executive_summary,
            key_topics=intelligence.key_topics,
            timeline=[event.model_dump(mode="json") for event in intelligence.timeline],
            risks=[risk.model_dump(mode="json") for risk in intelligence.risks],
            behavioral_metrics=metrics,
            mom_recipients=selected_recipients,
        )
        meeting.action_items = [
            ActionItem(
                description=task.description,
                owner=task.owner,
                due_date=task.deadline,
                due_time=task.deadline_time,
                status=TaskStatus.PROMISED,
            )
            for task in intelligence.action_items
        ]
        meeting.decisions = [
            Decision(
                statement=decision.statement,
                state=DecisionState(decision.state.value),
                rationale=decision.rationale,
                timestamp_seconds=decision.timestamp_seconds,
            )
            for decision in intelligence.decisions
        ]
        db.add(meeting)
        db.flush()

        track_action_item_drift(db, meeting.id)

        indexed_meeting_id = meeting.id
        index_meeting_transcript(
            meeting.id,
            transcript,
            transcript_segments=meeting.transcript_segments,
        )
        db.commit()
        delivery_report: dict[str, object] = {
            "status": "not_requested",
            "sent": [],
            "unsubscribed": [],
            "failed": [],
        }
        if selected_recipients:
            try:
                service_report = send_mom_emails(
                    meeting_title=meeting.title,
                    summary=meeting.executive_summary or "",
                    tasks=list(meeting.action_items),
                    recipients=selected_recipients,
                    db_session=db,
                )
                sent = service_report["sent"]
                failed = service_report["failed"]
                if sent and failed:
                    delivery_status = "partial"
                elif sent:
                    delivery_status = "sent"
                elif service_report["unsubscribed"]:
                    delivery_status = "skipped_all_unsubscribed"
                else:
                    delivery_status = "failed"
                delivery_report = {"status": delivery_status, **service_report}
                if sent:
                    meeting.mom_sent_at = utc_now()
            except Exception as error:
                logger.warning(
                    "Automatic MOM delivery failed for meeting %s (%s)",
                    meeting.id,
                    type(error).__name__,
                )
                delivery_report = {
                    "status": "failed",
                    "sent": [],
                    "unsubscribed": [],
                    "failed": selected_recipients,
                    "message": "The meeting was processed, but MOM email delivery failed. Check the SMTP settings and meeting email status.",
                }
            meeting.mom_delivery_report = delivery_report
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not persist MOM delivery status for meeting %s", meeting.id)
        return {
            "meeting_id": meeting.id,
            "intelligence": intelligence.model_dump(mode="json"),
            "behavioral_metrics": metrics,
            "diarization_status": meeting.diarization_status,
            "speaker_identities": meeting.speaker_identities,
            "transcript_segment_count": len(meeting.transcript_segments),
            "mom_delivery": delivery_report,
        }
    except HTTPException:
        db.rollback()
        if indexed_meeting_id is not None:
            try:
                delete_meeting_transcript(indexed_meeting_id)
            except Exception:
                logger.exception("Failed to remove RAG chunks after audio upload rollback")
        audio_path.unlink(missing_ok=True)
        raise
    except LLMProviderUnavailableError as error:
        db.rollback()
        if indexed_meeting_id is not None:
            try:
                delete_meeting_transcript(indexed_meeting_id)
            except Exception:
                logger.warning("Failed to clean up RAG chunks after upload rollback")
        audio_path.unlink(missing_ok=True)
        logger.warning("Audio ingestion failed during %s (%s)", failure_stage, type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Gemini intelligence extraction is temporarily unavailable: {error}",
        ) from error
    except LLMStructuredOutputError as error:
        db.rollback()
        if indexed_meeting_id is not None:
            try:
                delete_meeting_transcript(indexed_meeting_id)
            except Exception:
                logger.warning("Failed to clean up RAG chunks after invalid Gemini output")
        audio_path.unlink(missing_ok=True)
        logger.warning("Gemini structured-output validation failed during %s", failure_stage)
        raise HTTPException(
            status_code=502,
            detail=f"Gemini intelligence extraction failed: {error}",
        ) from error
    except Exception as error:
        db.rollback()
        if indexed_meeting_id is not None:
            try:
                delete_meeting_transcript(indexed_meeting_id)
            except Exception:
                logger.warning("Failed to clean up RAG chunks after audio upload rollback")
        audio_path.unlink(missing_ok=True)
        provider_status = getattr(error, "status_code", None)
        logger.warning(
            "Audio ingestion failed during %s (%s; provider_status=%s)",
            failure_stage,
            type(error).__name__,
            provider_status,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"{failure_stage} failed ({type(error).__name__}). "
                "Check the backend log for the provider status; transcript contents and credentials are not logged."
            ),
        ) from error
    finally:
        file.file.close()
