"""Transcribe uploaded meeting recordings with AssemblyAI's prerecorded API."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from app.core.config import settings
from app.schemas.domain import AudioProcessingResult, TranscriptSegment


logger = logging.getLogger(__name__)
ASSEMBLYAI_API_BASE = "https://api.assemblyai.com/v2"
UPLOAD_TIMEOUT = (15, 600)
REQUEST_TIMEOUT = (15, 60)


class AssemblyAIConfigurationError(RuntimeError):
    """The AssemblyAI API key is missing or invalid in local configuration."""


class AssemblyAITranscriptionError(RuntimeError):
    """AssemblyAI rejected the upload or failed to produce a transcript."""

    def __init__(self, message: str, *, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status


class AssemblyAITranscriptionTimeout(TimeoutError):
    """AssemblyAI did not finish the prerecorded transcription before the limit."""


def _api_key() -> str:
    configured = settings.assemblyai_api_key
    token = configured.get_secret_value().strip() if configured else ""
    if not token or token.casefold().startswith("replace-with"):
        raise AssemblyAIConfigurationError(
            "AssemblyAI is not configured. Set ASSEMBLYAI_API_KEY in backend/.env."
        )
    return token


def _request_json(response: requests.Response, operation: str) -> dict[str, Any]:
    if not response.ok:
        logger.warning("AssemblyAI %s request failed (upstream_status=%s)", operation, response.status_code)
        raise AssemblyAITranscriptionError(
            f"AssemblyAI {operation} request failed.",
            upstream_status=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise AssemblyAITranscriptionError(
            f"AssemblyAI returned an invalid response during {operation}."
        ) from error
    if not isinstance(payload, dict):
        raise AssemblyAITranscriptionError(
            f"AssemblyAI returned an invalid response during {operation}."
        )
    return payload


def _upload_audio(audio_path: Path, headers: dict[str, str]) -> str:
    try:
        with audio_path.open("rb") as audio_file:
            response = requests.post(
                f"{ASSEMBLYAI_API_BASE}/upload",
                headers={**headers, "Content-Type": "application/octet-stream"},
                data=audio_file,
                timeout=UPLOAD_TIMEOUT,
            )
    except requests.Timeout as error:
        raise AssemblyAITranscriptionTimeout("The recording upload to AssemblyAI timed out.") from error
    except requests.RequestException as error:
        raise AssemblyAITranscriptionError("Could not upload the recording to AssemblyAI.") from error
    except OSError as error:
        raise AssemblyAITranscriptionError("Could not read the saved recording for transcription.") from error

    payload = _request_json(response, "audio upload")
    upload_url = payload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise AssemblyAITranscriptionError("AssemblyAI did not return a valid audio upload URL.")
    return upload_url


def _submit_transcription(upload_url: str, headers: dict[str, str]) -> str:
    request_body = {
        "audio_url": upload_url,
        "speech_models": [settings.assemblyai_speech_model],
        "language_detection": True,
        "speaker_labels": True,
    }
    try:
        response = requests.post(
            f"{ASSEMBLYAI_API_BASE}/transcript",
            headers={**headers, "Content-Type": "application/json"},
            json=request_body,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as error:
        raise AssemblyAITranscriptionTimeout("AssemblyAI did not accept the transcription request in time.") from error
    except requests.RequestException as error:
        raise AssemblyAITranscriptionError("Could not start AssemblyAI transcription.") from error

    payload = _request_json(response, "transcription submission")
    transcript_id = payload.get("id")
    if not isinstance(transcript_id, str) or not transcript_id.strip():
        raise AssemblyAITranscriptionError("AssemblyAI did not return a transcription job ID.")
    return transcript_id


def _poll_transcription(transcript_id: str, headers: dict[str, str]) -> dict[str, Any]:
    deadline = time.monotonic() + settings.assemblyai_transcription_timeout_seconds
    poll_url = f"{ASSEMBLYAI_API_BASE}/transcript/{transcript_id}"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssemblyAITranscriptionTimeout(
                "AssemblyAI transcription exceeded the configured time limit."
            )
        try:
            response = requests.get(poll_url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.Timeout as error:
            raise AssemblyAITranscriptionTimeout("AssemblyAI status polling timed out.") from error
        except requests.RequestException as error:
            raise AssemblyAITranscriptionError("Could not retrieve the AssemblyAI transcription result.") from error

        payload = _request_json(response, "transcription polling")
        state = payload.get("status")
        if state == "completed":
            return payload
        if state == "error":
            logger.warning("AssemblyAI transcription job failed (job_id=%s)", transcript_id)
            raise AssemblyAITranscriptionError("AssemblyAI could not transcribe this recording.")
        if state not in {"queued", "processing"}:
            raise AssemblyAITranscriptionError("AssemblyAI returned an unknown transcription status.")
        time.sleep(min(settings.assemblyai_poll_interval_seconds, max(0.0, remaining)))


def _segments_from_utterances(
    utterances: object,
) -> tuple[list[TranscriptSegment], bool]:
    if not isinstance(utterances, list):
        return [], False

    speaker_ids: dict[str, str] = {}
    segments: list[TranscriptSegment] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        text = utterance.get("text")
        start_ms = utterance.get("start")
        end_ms = utterance.get("end")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(start_ms, (int, float))
            or isinstance(start_ms, bool)
            or not isinstance(end_ms, (int, float))
            or isinstance(end_ms, bool)
        ):
            continue
        start_seconds = max(0.0, float(start_ms) / 1_000)
        end_seconds = max(0.0, float(end_ms) / 1_000)
        if end_seconds <= start_seconds:
            continue

        raw_speaker = utterance.get("speaker")
        speaker_id = None
        if isinstance(raw_speaker, str) and raw_speaker.strip():
            raw_speaker = raw_speaker.strip()
            if raw_speaker not in speaker_ids:
                speaker_ids[raw_speaker] = f"SPEAKER_{len(speaker_ids):02d}"
            speaker_id = speaker_ids[raw_speaker]
        segments.append(
            TranscriptSegment(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                speaker_id=speaker_id,
                text=text.strip(),
            )
        )
    return segments, bool(speaker_ids)


def transcribe_recording(audio_path: Path) -> AudioProcessingResult:
    """Upload and transcribe one recording, including timestamped speaker labels.

    Audio is sent to AssemblyAI's prerecorded transcription API. PII redaction
    still occurs locally in the shared meeting-persistence pipeline before the
    transcript is passed to Gemini or indexed in ChromaDB.
    """
    if not isinstance(audio_path, Path):
        raise TypeError("audio_path must be a pathlib.Path")
    try:
        resolved_path = audio_path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}") from error
    if not resolved_path.is_file() or resolved_path.stat().st_size <= 0:
        raise ValueError("audio_path must refer to a non-empty regular file")

    headers = {"authorization": _api_key()}
    upload_url = _upload_audio(resolved_path, headers)
    transcript_id = _submit_transcription(upload_url, headers)
    result = _poll_transcription(transcript_id, headers)

    transcript = result.get("text")
    if not isinstance(transcript, str):
        transcript = ""
    transcript = transcript.strip()

    segments, has_speaker_labels = _segments_from_utterances(result.get("utterances"))
    duration = result.get("audio_duration")
    duration_seconds = (
        max(0.0, float(duration))
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else max((segment.end_seconds for segment in segments), default=0.0)
    )

    if not transcript and segments:
        transcript = " ".join(segment.text for segment in segments).strip()
    if transcript and not segments:
        duration_seconds = max(duration_seconds, 0.001)
        segments = [
            TranscriptSegment(
                start_seconds=0.0,
                end_seconds=duration_seconds,
                speaker_id=None,
                text=transcript,
            )
        ]

    return AudioProcessingResult(
        transcript=transcript,
        transcript_segments=segments,
        duration_seconds=duration_seconds,
        diarization_status="completed" if has_speaker_labels else "not_configured",
    )
