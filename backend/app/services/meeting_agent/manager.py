"""Lifecycle manager for the local Playwright meeting assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.core.config import PROJECT_ROOT, settings
from app.services.meeting_agent.audio_capture import AudioCaptureError, SystemAudioCapture


logger = logging.getLogger(__name__)
RECORDING_DIRECTORY = PROJECT_ROOT / "data" / "live_recordings"
ACTIVE_STATES = {"starting", "waiting_for_user", "waiting_for_admission", "recording", "stopping", "processing"}
MEETING_HOSTS = {"meet.google.com", "teams.microsoft.com", "teams.live.com"}


@dataclass
class MeetingAgentSession:
    id: str
    title: str
    meeting_url: str
    recipients: list[str]
    started_at: datetime
    audio_path: Path
    status: str = "starting"
    message: str = "Preparing the local browser and audio capture."
    error: str | None = None
    meeting_id: int | None = None
    audio_capture: SystemAudioCapture | None = None
    audio_capture_started: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    finished_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


def validate_meeting_url(value: str) -> str:
    """Accept only HTTPS Google Meet and Microsoft Teams meeting URLs."""
    normalized = value.strip()
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").casefold().rstrip(".")
    is_allowed_host = host in MEETING_HOSTS or any(
        host.endswith(f".{allowed}") for allowed in MEETING_HOSTS
    )
    if (
        parsed.scheme != "https"
        or not is_allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.strip("/")
    ):
        raise ValueError("Use a valid HTTPS Google Meet or Microsoft Teams meeting link.")
    return normalized


class MeetingAgentManager:
    """Own one visible Playwright session and its local audio file at a time."""

    def __init__(self) -> None:
        self._sessions: dict[str, MeetingAgentSession] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        meeting_url: str,
        title: str,
        recipients: list[str],
    ) -> dict[str, object]:
        if not settings.meeting_agent_enabled:
            raise RuntimeError("The live meeting assistant is disabled in backend settings.")
        safe_url = validate_meeting_url(meeting_url)
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 500:
            raise ValueError("title must contain between 1 and 500 characters")

        async with self._lock:
            if any(session.status in ACTIVE_STATES for session in self._sessions.values()):
                raise RuntimeError("A live meeting session is already active.")
            session_id = uuid4().hex
            session = MeetingAgentSession(
                id=session_id,
                title=normalized_title,
                meeting_url=safe_url,
                recipients=recipients,
                started_at=datetime.now(timezone.utc),
                audio_path=RECORDING_DIRECTORY / f"{session_id}.wav",
                audio_capture=SystemAudioCapture(RECORDING_DIRECTORY / f"{session_id}.wav"),
            )
            self._sessions[session.id] = session
            session.task = asyncio.create_task(self._run(session), name=f"amip-meeting-{session.id}")
            return self._public_status(session)

    async def stop(self, session_id: str) -> dict[str, object]:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("Live meeting session not found")
        if session.status not in ACTIVE_STATES:
            return self._public_status(session)
        session.status = "stopping"
        session.message = "Stopping capture and handing the recording to meeting processing."
        session.stop_event.set()
        return self._public_status(session)

    def get_status(self, session_id: str) -> dict[str, object] | None:
        session = self._sessions.get(session_id)
        return self._public_status(session) if session is not None else None

    def get_active_status(self) -> dict[str, object] | None:
        session = next(
            (item for item in reversed(tuple(self._sessions.values())) if item.status in ACTIVE_STATES),
            None,
        )
        return self._public_status(session) if session is not None else None

    def get_recording(self, session_id: str) -> Path | None:
        session = self._sessions.get(session_id)
        if session is None or session.status != "failed" or not session.audio_path.is_file():
            return None
        return session.audio_path

    async def shutdown(self) -> None:
        sessions = [session for session in self._sessions.values() if session.status in ACTIVE_STATES]
        for session in sessions:
            session.stop_event.set()
        tasks = [session.task for session in sessions if session.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, session: MeetingAgentSession) -> None:
        browser = None
        try:
            from playwright.async_api import async_playwright

            session.message = "Opening the meeting in a visible browser window."
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=settings.meeting_agent_browser_headless,
                    ignore_default_args=["--mute-audio"],
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--use-fake-device-for-media-stream",
                    ],
                )
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(session.meeting_url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(3_000)
                await self._prepare_join(page)
                session.status = "waiting_for_user"
                session.message = (
                    f"The browser is open as {settings.meeting_agent_name}. Complete any sign-in or "
                    "host admission in that window; capture starts only after the meeting is joined."
                )

                while not session.stop_event.is_set():
                    if page.is_closed():
                        raise RuntimeError("The meeting browser window was closed before recording stopped.")
                    if await self._is_inside_meeting(page):
                        if session.status != "recording":
                            assert session.audio_capture is not None
                            await asyncio.to_thread(session.audio_capture.start)
                            if not session.audio_path.is_file():
                                raise FileNotFoundError(
                                    "Audio capture initialized without creating its WAV file."
                                )
                            session.audio_capture_started = True
                            session.status = "recording"
                            session.message = "Meeting audio is being captured locally. Use Stop to finish and process it."
                    elif session.status != "recording":
                        session.status = "waiting_for_admission"
                        session.message = (
                            "Waiting for the meeting to admit the AMIP assistant. If the browser requests "
                            "sign-in or another step, complete it there."
                        )
                    await asyncio.sleep(1)

                if session.audio_capture is None or not session.audio_capture_started:
                    raise RuntimeError(
                        "No meeting audio was captured. Wait until the dashboard shows Recording before stopping. "
                        "For Google Meet, finish any sign-in and wait for host admission in the visible browser."
                    )
                session.status = "processing"
                session.message = "Processing the local recording through the existing audio upload pipeline."
                await asyncio.to_thread(session.audio_capture.stop)
                if not session.audio_path.is_file():
                    raise FileNotFoundError(
                        "Audio capture stopped without leaving a WAV file. Check the local audio device and permissions."
                    )
                await browser.close()
                browser = None
                await asyncio.to_thread(self._process_with_existing_pipeline, session)
        except asyncio.CancelledError:
            session.status = "failed"
            session.error = "The live session was interrupted while the backend was stopping."
            raise
        except Exception as error:
            session.status = "failed"
            if isinstance(error, AudioCaptureError):
                session.error = str(error)
            elif isinstance(error, RuntimeError) and str(error).startswith("The assistant did not join"):
                session.error = str(error)
            elif isinstance(error, RuntimeError) and str(error).startswith("No meeting audio was captured"):
                session.error = str(error)
            elif isinstance(error, FileNotFoundError) and not session.audio_path.is_file():
                session.error = (
                    "No WAV was saved, so there is nothing to download. Wait until the dashboard shows Recording "
                    "before stopping, and check that system audio capture is configured."
                )
            elif type(error).__name__ == "Error" and "Executable doesn't exist" in str(error):
                session.error = "Playwright Chromium is not installed. Run `playwright install chromium` in the backend virtual environment."
            else:
                session.error = f"The live meeting session failed ({type(error).__name__}). Check local browser and audio setup."
            session.message = "Live capture stopped. You can download a saved recording if one is available, or use manual upload."
            logger.exception(
                "Live meeting session failed (%s; capture_started=%s; recording_exists=%s)",
                type(error).__name__,
                session.audio_capture_started,
                session.audio_path.is_file(),
            )
        finally:
            if session.audio_capture is not None:
                try:
                    await asyncio.to_thread(session.audio_capture.stop)
                except Exception as error:
                    logger.debug("Could not finalize local audio capture (%s)", type(error).__name__)
            if browser is not None:
                try:
                    await browser.close()
                except Exception as error:
                    logger.debug("Could not close Playwright browser (%s)", type(error).__name__)
            if session.status == "processing" and session.meeting_id is None:
                session.status = "failed"
                session.error = "The recording did not produce a meeting record. Use manual upload to retry it."
            session.finished_event.set()

    async def _prepare_join(self, page: object) -> None:
        """Fill a guest display name, mute fake devices, and submit the join action.

        The assistant does not bypass sign-in, CAPTCHA, lobby admission, or host
        controls. An operator can finish those steps in the visible browser.
        """
        name_selectors = (
            'input[placeholder*="Your name" i]',
            'input[aria-label*="Your name" i]',
            'input[placeholder="Name" i]',
        )
        for selector in name_selectors:
            field = page.locator(selector).first
            if await field.count() and await field.is_visible():
                await field.fill(settings.meeting_agent_name)
                break

        for label in (re.compile(r"turn off microphone|mute microphone|^mute$", re.I),
                      re.compile(r"turn off camera|turn off video|^turn camera off$", re.I)):
            button = page.get_by_role("button", name=label).first
            if await button.count() and await button.is_visible() and await button.is_enabled():
                await button.click(timeout=2_000)

        join_button = page.get_by_role(
            "button",
            name=re.compile(r"^(join now|ask to join|request to join|join meeting)$", re.I),
        ).first
        if await join_button.count() and await join_button.is_visible() and await join_button.is_enabled():
            await join_button.click(timeout=5_000)

    @staticmethod
    async def _is_inside_meeting(page: object) -> bool:
        for label in (re.compile(r"leave call|leave meeting|hang up", re.I),):
            button = page.get_by_role("button", name=label).first
            if await button.count() and await button.is_visible():
                return True
        return False

    @staticmethod
    def _process_with_existing_pipeline(session: MeetingAgentSession) -> None:
        """Run the exact manual-upload endpoint against the completed local WAV."""
        from fastapi import UploadFile

        from app.api.v1.endpoints.audio import upload_audio
        from app.core.database import SessionLocal

        database = SessionLocal()
        try:
            with session.audio_path.open("rb") as recording:
                uploaded_file = UploadFile(filename=session.audio_path.name, file=recording)
                response = upload_audio(
                    file=uploaded_file,
                    title=session.title,
                    recipients=json.dumps(session.recipients),
                    db=database,
                )
            session.meeting_id = int(response["meeting_id"])
            session.status = "completed"
            session.message = "Meeting processing completed. The meeting is available in your library."
            session.audio_path.unlink(missing_ok=True)
        except Exception as error:
            detail = getattr(error, "detail", None)
            if isinstance(detail, str) and len(detail) <= 500:
                session.error = detail
            elif not session.audio_path.is_file():
                session.error = (
                    "The captured WAV could not be found, so there is no recording to download. "
                    "Check that the dashboard reached Recording before Stop and process was clicked."
                )
            else:
                session.error = (
                    f"Meeting processing failed ({type(error).__name__}). "
                    "The captured WAV is available to download and retry with manual upload."
                )
            session.status = "failed"
            session.message = "Live capture ended, but processing failed. Download the WAV and retry with manual upload."
            logger.exception(
                "Live recording processing failed (%s; recording_exists=%s)",
                type(error).__name__,
                session.audio_path.is_file(),
            )
        finally:
            database.close()

    @staticmethod
    def _public_status(session: MeetingAgentSession) -> dict[str, object]:
        recording_available = session.status == "failed" and session.audio_path.is_file()
        return {
            "session_id": session.id,
            "title": session.title,
            "agent_name": settings.meeting_agent_name,
            "status": session.status,
            "message": session.message,
            "error": session.error,
            "meeting_id": session.meeting_id,
            "recording_available": recording_available,
            "recording_url": f"/api/v1/meeting-agent/{session.id}/recording"
            if recording_available
            else None,
            "started_at": session.started_at.isoformat(),
        }


meeting_agent_manager = MeetingAgentManager()
