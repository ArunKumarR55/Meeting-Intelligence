"""AssemblyAI browser-token proxy for live transcript processing."""

import logging
import re
from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.meeting_processing import persist_meeting_transcript


logger = logging.getLogger(__name__)
router = APIRouter()
EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")
MAX_TRANSCRIPT_CHARACTERS = 2_000_000
ASSEMBLYAI_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"
TOKEN_LIFETIME_SECONDS = 600


class LiveTranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    start_seconds: Annotated[float, Field(ge=0, le=86_400)] | None = None
    end_seconds: Annotated[float, Field(ge=0, le=86_400)] | None = None
    speaker_label: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @field_validator("end_seconds")
    @classmethod
    def validate_end_after_start(cls, value: float | None, info: ValidationInfo) -> float | None:
        start = info.data.get("start_seconds")
        if value is not None and start is not None and value < start:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")
        return value


class ProcessMeetingTranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Annotated[str, Field(min_length=1, max_length=500)]
    transcript: Annotated[str, Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARACTERS)]
    recipients: list[Annotated[str, Field(min_length=3, max_length=320)]] = Field(
        default_factory=list, max_length=500
    )
    transcript_segments: list[LiveTranscriptSegment] = Field(default_factory=list, max_length=20_000)

    @model_validator(mode="after")
    def bound_segment_text(self) -> "ProcessMeetingTranscriptRequest":
        if sum(len(segment.text) for segment in self.transcript_segments) > MAX_TRANSCRIPT_CHARACTERS:
            raise ValueError("Combined transcript segment text exceeds the 2,000,000 character limit")
        return self

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for email in values:
            email = email.strip()
            if not EMAIL_PATTERN.fullmatch(email) or "\r" in email or "\n" in email:
                raise ValueError("Every recipient must be a valid email address")
            if email.casefold() not in seen:
                normalized.append(email)
                seen.add(email.casefold())
        return normalized


@router.get("/token", summary="Mint a short-lived AssemblyAI browser token")
def get_assemblyai_token() -> dict[str, object]:
    """Keep the AssemblyAI API key on the backend and mint a short-lived stream token."""
    api_key = settings.assemblyai_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AssemblyAI is not configured. Set ASSEMBLYAI_API_KEY in backend/.env.",
        )

    try:
        response = requests.get(
            ASSEMBLYAI_TOKEN_URL,
            params={"expires_in_seconds": TOKEN_LIFETIME_SECONDS},
            headers={"Authorization": api_key.get_secret_value()},
            timeout=(5, 15),
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            logger.error("AssemblyAI token endpoint returned an invalid response")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AssemblyAI did not return a usable temporary token.",
            )
        return {"token": token, "expires_in_seconds": TOKEN_LIFETIME_SECONDS}
    except HTTPException:
        raise
    except requests.RequestException as error:
        upstream_status = error.response.status_code if error.response is not None else None
        logger.warning("AssemblyAI token request failed (upstream_status=%s)", upstream_status)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not obtain an AssemblyAI streaming token. Check the API key and provider availability.",
        ) from error
    except ValueError as error:
        logger.warning("AssemblyAI token endpoint returned malformed JSON")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AssemblyAI returned an invalid token response.",
        ) from error


@router.post(
    "/process_meeting",
    status_code=status.HTTP_201_CREATED,
    summary="Analyze and store a live meeting transcript",
)
def process_meeting_transcript(
    request: ProcessMeetingTranscriptRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Send the browser transcript through the existing Gemini and RAG pipeline."""
    supplied_transcript = request.transcript.strip()
    if not supplied_transcript:
        raise HTTPException(status_code=422, detail="The transcript is empty")

    speaker_ids: dict[str, str] = {}
    normalized_segments: list[dict[str, object]] = []
    for segment in request.transcript_segments:
        speaker_id = None
        segment_text = segment.text.strip()
        if segment.speaker_label:
            label = segment.speaker_label.strip()
            if label not in speaker_ids:
                speaker_ids[label] = f"SPEAKER_{len(speaker_ids):02d}"
            speaker_id = speaker_ids[label]
            segment_text = f"Speaker {label}: {segment_text}"
        normalized_segments.append(
            {
                "text": segment_text,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "speaker_id": speaker_id,
            }
        )

    # Keep the joined transcript byte-for-byte aligned with the indexed turns so
    # timestamp citations can map transcript character positions to ASR times.
    transcript = (
        " ".join(str(segment["text"]) for segment in normalized_segments)
        if normalized_segments
        else supplied_transcript
    )
    if len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise HTTPException(status_code=422, detail="The processed transcript exceeds the 2,000,000 character limit")

    return persist_meeting_transcript(
        title=request.title,
        transcript=transcript,
        recipients=request.recipients,
        db=db,
        transcript_segments=normalized_segments,
        diarization_status="assemblyai_streaming" if speaker_ids else "not_configured",
    )
