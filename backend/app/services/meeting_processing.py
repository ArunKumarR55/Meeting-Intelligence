"""Shared transcript-to-meeting processing used by live and uploaded audio."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.domain import ActionItem, Decision, Meeting, TaskStatus, DecisionState, utc_now
from app.services.analytics.extractor import (
    LLMProviderUnavailableError,
    LLMStructuredOutputError,
    extract_meeting_intelligence,
)
from app.services.audio_ingestion.pii_redactor import redact_pii
from app.services.audio_ingestion.speaker_identity import infer_speaker_identities
from app.services.behavioral_analytics.metrics import calculate_participation_metrics
from app.services.corporate_memory.rag_store import delete_meeting_transcript, index_meeting_transcript
from app.services.integrations.email_sender import send_mom_emails
from app.services.workflows.action_item_drift import track_action_item_drift


logger = logging.getLogger(__name__)


def persist_meeting_transcript(
    *,
    title: str,
    transcript: str,
    recipients: list[str],
    db: Session,
    transcript_segments: Sequence[Mapping[str, Any]] = (),
    diarization_status: str = "not_configured",
    recording_path: str | None = None,
) -> dict[str, object]:
    """Redact, analyze, persist, index, and deliver MOM for an existing transcript."""
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 500:
        raise HTTPException(status_code=400, detail="title must contain between 1 and 500 characters")

    safe_transcript = redact_pii(transcript).strip()
    if not safe_transcript:
        raise HTTPException(status_code=422, detail="The transcript contains no usable speech")

    safe_segments: list[dict[str, object]] = []
    for item in transcript_segments:
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        speaker_id = item.get("speaker_id")
        start_seconds = item.get("start_seconds")
        end_seconds = item.get("end_seconds")
        segment: dict[str, object] = {"text": redact_pii(text).strip()}
        if isinstance(speaker_id, str) and speaker_id.strip():
            segment["speaker_id"] = speaker_id.strip()[:64]
        if isinstance(start_seconds, (int, float)) and not isinstance(start_seconds, bool):
            segment["start_seconds"] = max(0.0, float(start_seconds))
        if isinstance(end_seconds, (int, float)) and not isinstance(end_seconds, bool):
            segment["end_seconds"] = max(0.0, float(end_seconds))
        safe_segments.append(segment)

    meeting_reference_date = datetime.now(ZoneInfo(settings.reminder_timezone)).date()
    indexed_meeting_id: int | None = None
    failure_stage = "Gemini intelligence extraction"
    try:
        intelligence = extract_meeting_intelligence(safe_transcript, meeting_date=meeting_reference_date)
        failure_stage = "behavioral analytics calculation"
        metrics = calculate_participation_metrics(safe_transcript)
        speaker_identities = infer_speaker_identities(safe_segments)

        failure_stage = "meeting database and memory storage"
        meeting = Meeting(
            title=normalized_title,
            recording_path=recording_path,
            transcript=safe_transcript,
            transcript_segments=safe_segments,
            speaker_identities=speaker_identities,
            diarization_status=diarization_status,
            executive_summary=intelligence.executive_summary,
            key_topics=intelligence.key_topics,
            timeline=[event.model_dump(mode="json") for event in intelligence.timeline],
            risks=[risk.model_dump(mode="json") for risk in intelligence.risks],
            behavioral_metrics=metrics,
            mom_recipients=recipients,
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
            safe_transcript,
            transcript_segments=safe_segments,
        )
        db.commit()

        delivery_report: dict[str, object] = {
            "status": "not_requested",
            "sent": [],
            "unsubscribed": [],
            "failed": [],
        }
        if recipients:
            try:
                service_report = send_mom_emails(
                    meeting_title=meeting.title,
                    summary=meeting.executive_summary or "",
                    tasks=list(meeting.action_items),
                    recipients=recipients,
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
                    "failed": recipients,
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
            "transcript_segment_count": len(safe_segments),
            "mom_delivery": delivery_report,
        }
    except HTTPException:
        db.rollback()
        if indexed_meeting_id is not None:
            try:
                delete_meeting_transcript(indexed_meeting_id)
            except Exception:
                logger.exception("Failed to remove RAG chunks after transcript-processing rollback")
        raise
    except LLMProviderUnavailableError as error:
        db.rollback()
        if indexed_meeting_id is not None:
            _remove_index(indexed_meeting_id)
        logger.warning("Live transcript processing failed during %s (%s)", failure_stage, type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Gemini intelligence extraction is temporarily unavailable: {error}",
        ) from error
    except LLMStructuredOutputError as error:
        db.rollback()
        if indexed_meeting_id is not None:
            _remove_index(indexed_meeting_id)
        logger.warning("Gemini structured-output validation failed during live transcript processing")
        raise HTTPException(status_code=502, detail=f"Gemini intelligence extraction failed: {error}") from error
    except Exception as error:
        db.rollback()
        if indexed_meeting_id is not None:
            _remove_index(indexed_meeting_id)
        logger.exception("Live transcript processing failed during %s (%s)", failure_stage, type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail=(
                f"{failure_stage} failed ({type(error).__name__}). "
                "Check the backend log for details; transcript contents and credentials are not logged."
            ),
        ) from error


def _remove_index(meeting_id: int) -> None:
    try:
        delete_meeting_transcript(meeting_id)
    except Exception:
        logger.exception("Failed to remove RAG chunks after transcript-processing rollback")
