"""Audio upload and local intelligence processing endpoint."""

import logging
import json
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.core.database import get_db
from app.services.audio_ingestion.assemblyai_transcriber import (
    AssemblyAIConfigurationError,
    AssemblyAITranscriptionError,
    AssemblyAITranscriptionTimeout,
    transcribe_recording,
)
from app.services.meeting_processing import persist_meeting_transcript


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

    if settings.assemblyai_api_key is None:
        raise HTTPException(
            status_code=503,
            detail="AssemblyAI is not configured. Set ASSEMBLYAI_API_KEY in backend/.env.",
        )

    audio_path = _save_upload(file)
    failure_stage = "AssemblyAI prerecorded transcription"
    try:
        audio_result = transcribe_recording(audio_path)
        transcript = audio_result.transcript
        if not transcript.strip():
            raise HTTPException(
                status_code=422,
                detail="No speech could be transcribed from the uploaded audio",
            )
        transcript_segments = [
            segment.model_dump(mode="json")
            for segment in audio_result.transcript_segments
        ]
        result = persist_meeting_transcript(
            title=normalized_title,
            transcript=transcript,
            recipients=selected_recipients,
            db=db,
            transcript_segments=transcript_segments,
            diarization_status=audio_result.diarization_status,
            recording_path=str(audio_path.resolve()),
        )
        return result
    except HTTPException:
        db.rollback()
        audio_path.unlink(missing_ok=True)
        raise
    except AssemblyAIConfigurationError as error:
        db.rollback()
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AssemblyAITranscriptionTimeout as error:
        db.rollback()
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail=str(error)) from error
    except AssemblyAITranscriptionError as error:
        db.rollback()
        audio_path.unlink(missing_ok=True)
        suffix = f" (upstream HTTP {error.upstream_status})" if error.upstream_status else ""
        raise HTTPException(
            status_code=502,
            detail=f"{error}{suffix} Check the AssemblyAI API key, account status, and backend logs.",
        ) from error
    except Exception as error:
        db.rollback()
        audio_path.unlink(missing_ok=True)
        logger.warning(
            "Audio ingestion failed during %s (%s)",
            failure_stage,
            type(error).__name__,
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
