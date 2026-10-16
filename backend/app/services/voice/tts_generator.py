"""Generate local meeting briefing files using Microsoft Edge neural TTS."""

from pathlib import Path
from uuid import uuid4

import edge_tts

from app.core.config import PROJECT_ROOT
from app.services.audio_ingestion.pii_redactor import redact_pii


BRIEFINGS_DIRECTORY = PROJECT_ROOT / "data" / "briefings"
BRIEFING_VOICE = "en-US-ChristopherNeural"


async def generate_audio_briefing(summary_text: str, meeting_id: int) -> Path:
    """Synthesize a privacy-scrubbed summary and return its MP3 file path."""
    if not isinstance(summary_text, str):
        raise TypeError("summary_text must be a string")
    if isinstance(meeting_id, bool) or not isinstance(meeting_id, int) or meeting_id <= 0:
        raise ValueError("meeting_id must be a positive integer")

    safe_summary = redact_pii(summary_text).strip()
    if not safe_summary:
        raise ValueError("summary_text must contain non-whitespace content")

    BRIEFINGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = BRIEFINGS_DIRECTORY / f"meeting_{meeting_id}_briefing.mp3"
    temporary_path = BRIEFINGS_DIRECTORY / f".meeting_{meeting_id}_{uuid4().hex}.mp3"

    try:
        communicator = edge_tts.Communicate(
            text=safe_summary,
            voice=BRIEFING_VOICE,
        )
        await communicator.save(str(temporary_path))
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return output_path
