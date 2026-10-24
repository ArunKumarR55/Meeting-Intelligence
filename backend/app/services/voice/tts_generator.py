"""Generate cached, privacy-scrubbed meeting briefings with Edge neural TTS."""

import asyncio
import hashlib
import logging
from pathlib import Path
import re
from uuid import uuid4

import edge_tts

from app.core.config import PROJECT_ROOT
from app.services.audio_ingestion.pii_redactor import redact_pii


BRIEFINGS_DIRECTORY = PROJECT_ROOT / "data" / "briefings"
BRIEFING_VOICES = ("en-US-ChristopherNeural", "en-US-AriaNeural")
MAX_SYNTHESIS_ATTEMPTS = 3

logger = logging.getLogger(__name__)


async def generate_audio_briefing(summary_text: str, meeting_id: int) -> Path:
    """Synthesize a privacy-scrubbed summary and return its MP3 file path."""
    if not isinstance(summary_text, str):
        raise TypeError("summary_text must be a string")
    if isinstance(meeting_id, bool) or not isinstance(meeting_id, int) or meeting_id <= 0:
        raise ValueError("meeting_id must be a positive integer")

    safe_summary = " ".join(redact_pii(summary_text).split())
    if not safe_summary:
        raise ValueError("summary_text must contain non-whitespace content")

    BRIEFINGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(safe_summary.encode("utf-8")).hexdigest()[:16]
    output_path = BRIEFINGS_DIRECTORY / f"meeting_{meeting_id}_{content_hash}.mp3"
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    temporary_path = BRIEFINGS_DIRECTORY / f".meeting_{meeting_id}_{uuid4().hex}.mp3"
    last_error: Exception | None = None
    try:
        for attempt in range(MAX_SYNTHESIS_ATTEMPTS):
            voice = BRIEFING_VOICES[min(attempt, len(BRIEFING_VOICES) - 1)]
            try:
                communicator = edge_tts.Communicate(text=safe_summary, voice=voice)
                await communicator.save(str(temporary_path))
                if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                    raise RuntimeError("The speech provider returned an empty audio file")
                temporary_path.replace(output_path)
                return output_path
            except Exception as error:
                last_error = error
                logger.warning(
                    "Meeting briefing synthesis attempt %s failed (meeting_id=%s, voice=%s, error=%s)",
                    attempt + 1,
                    meeting_id,
                    voice,
                    type(error).__name__,
                )
                temporary_path.unlink(missing_ok=True)
                if attempt + 1 < MAX_SYNTHESIS_ATTEMPTS:
                    await asyncio.sleep(0.5 * (2 ** attempt))
    finally:
        temporary_path.unlink(missing_ok=True)

    raise RuntimeError("The speech provider could not generate this meeting recap after retries") from last_error
