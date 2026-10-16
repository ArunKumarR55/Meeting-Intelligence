"""Control a locally running Playwright meeting assistant."""

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.meeting_agent.manager import meeting_agent_manager


router = APIRouter()
EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


class StartMeetingAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    meeting_url: Annotated[str, Field(min_length=12, max_length=2048)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    recipients: list[Annotated[str, Field(min_length=3, max_length=320)]] = Field(default_factory=list, max_length=500)
    consent_to_record: bool

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

    @model_validator(mode="after")
    def require_recording_consent(self) -> "StartMeetingAgentRequest":
        if not self.consent_to_record:
            raise ValueError("Confirm that you have permission to record and will notify meeting participants")
        return self


@router.post("/start", status_code=202, summary="Start a local live meeting session")
async def start_meeting_agent(request: StartMeetingAgentRequest) -> dict[str, object]:
    """Open the supported meeting link and capture audio after joining."""
    try:
        return await meeting_agent_manager.start(
            meeting_url=request.meeting_url,
            title=request.title,
            recipients=request.recipients,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        status_code = 503 if "disabled" in str(error).casefold() else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("/active", summary="Get the active live meeting session, if any")
def get_active_meeting_agent_status() -> dict[str, object] | None:
    return meeting_agent_manager.get_active_status()


@router.get("/{session_id}", summary="Get live meeting session status")
def get_meeting_agent_status(session_id: str) -> dict[str, object]:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise HTTPException(status_code=404, detail="Live meeting session not found")
    result = meeting_agent_manager.get_status(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Live meeting session not found")
    return result


@router.post("/{session_id}/stop", status_code=202, summary="Stop capture and process the recording")
async def stop_meeting_agent(session_id: str) -> dict[str, object]:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise HTTPException(status_code=404, detail="Live meeting session not found")
    try:
        return await meeting_agent_manager.stop(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{session_id}/recording", summary="Download a failed live capture for manual upload")
def download_live_recording(session_id: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    recording = meeting_agent_manager.get_recording(session_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="No saved recording is available")
    return FileResponse(
        path=recording,
        media_type="audio/wav",
        filename=f"amip-live-{session_id[:8]}.wav",
    )
