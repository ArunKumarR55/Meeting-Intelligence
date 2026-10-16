"""Meeting memory retrieval and spoken briefing endpoints."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.db.models.domain import Meeting
from app.services.corporate_memory.answering import (
    MemoryAnswerGenerationError,
    MemoryProviderUnavailableError,
    answer_memory_query,
)
from app.services.corporate_memory.rag_store import query_memory
from app.services.voice.tts_generator import generate_audio_briefing


logger = logging.getLogger(__name__)
router = APIRouter()


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=10_000)
    meeting_id: int | None = Field(default=None, gt=0)


@router.post("/query", summary="Search transcript memory")
def search_meeting_memory(
    request: MemoryQuery,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Answer from retrieved transcript evidence and include verifiable citations."""
    try:
        matches = query_memory(
            request.query,
            n_results=5,
            meeting_id=request.meeting_id,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.warning("Meeting memory retrieval failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="Meeting memory search is unavailable") from error

    if not matches:
        return {
            "query": request.query,
            "answer": "No matching transcript excerpts were found.",
            "citations": [],
            "result_count": 0,
        }

    meeting_ids = {
        match.get("meeting_id")
        for match in matches
        if isinstance(match.get("meeting_id"), int)
    }
    meetings = db.scalars(select(Meeting).where(Meeting.id.in_(meeting_ids))).all()
    meetings_by_id = {meeting.id: meeting for meeting in meetings}

    enriched_matches: list[dict[str, object]] = []
    for match in matches:
        meeting = meetings_by_id.get(match.get("meeting_id"))
        enriched = {**match, "meeting_title": meeting.title if meeting else None}
        if meeting and enriched.get("start_seconds") is None:
            start_seconds, end_seconds = _segment_timestamps_for_chunk(
                meeting.transcript_segments,
                match.get("character_start"),
                match.get("character_end"),
            )
            enriched["start_seconds"] = start_seconds
            enriched["end_seconds"] = end_seconds
        enriched_matches.append(enriched)

    try:
        intelligence = answer_memory_query(request.query, enriched_matches)
    except MemoryProviderUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except MemoryAnswerGenerationError as error:
        logger.warning("Meeting memory answer did not satisfy citation requirements (%s)", error)
        raise HTTPException(
            status_code=502,
            detail=f"Could not generate a cited answer: {error}",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Meeting memory answers are temporarily unavailable",
        ) from error
    except Exception as error:
        logger.warning("Meeting memory answer failed (%s)", type(error).__name__)
        raise HTTPException(status_code=502, detail="Could not generate a cited answer") from error

    matches_by_id = {str(match["id"]): match for match in enriched_matches}
    citations: list[dict[str, object]] = []
    for source_id in intelligence.source_ids:
        match = matches_by_id[source_id]
        meeting_id = match.get("meeting_id")
        chunk_index = match.get("chunk_index")
        meeting_title = match.get("meeting_title") or f"Meeting {meeting_id}"
        start_seconds = match.get("start_seconds")
        end_seconds = match.get("end_seconds")
        timestamp_label = (
            _format_timestamp(float(start_seconds))
            if isinstance(start_seconds, (float, int))
            else None
        )
        label = f"{meeting_title} · {timestamp_label}" if timestamp_label else f"{meeting_title} · transcript chunk {chunk_index}"
        citations.append(
            {
                "source_id": source_id,
                "meeting_id": meeting_id,
                "meeting_title": meeting_title,
                "chunk_index": chunk_index,
                "character_start": match.get("character_start"),
                "character_end": match.get("character_end"),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "timestamp_label": timestamp_label,
                "label": label,
            }
        )
    return {
        "query": request.query,
        "answer": intelligence.answer,
        "citations": citations,
        "result_count": len(enriched_matches),
    }


def _segment_timestamps_for_chunk(
    segments: list[dict[str, object]],
    character_start: object,
    character_end: object,
) -> tuple[float | None, float | None]:
    """Resolve legacy Chroma chunks to times using saved ASR segment offsets."""
    if not isinstance(character_start, int) or not isinstance(character_end, int):
        return None, None
    cursor = 0
    overlaps: list[tuple[float, float]] = []
    for segment in segments:
        text = segment.get("text")
        if not isinstance(text, str) or not text:
            continue
        segment_start = cursor
        segment_end = segment_start + len(text)
        if min(character_end, segment_end) > max(character_start, segment_start):
            start_seconds = segment.get("start_seconds")
            end_seconds = segment.get("end_seconds")
            if isinstance(start_seconds, (float, int)) and isinstance(end_seconds, (float, int)):
                overlaps.append((float(start_seconds), float(end_seconds)))
        cursor = segment_end + 1
    if not overlaps:
        return None, None
    return min(start for start, _end in overlaps), max(end for _start, end in overlaps)


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@router.get("/briefing/{meeting_id}", summary="Stream a spoken meeting briefing")
async def get_meeting_briefing(
    meeting_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Synthesize the stored summary and stream its MP3 audio."""
    meeting = db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.executive_summary or not meeting.executive_summary.strip():
        raise HTTPException(status_code=422, detail="Meeting has no executive summary")

    try:
        briefing_path: Path = await generate_audio_briefing(
            meeting.executive_summary,
            meeting.id,
        )
    except Exception as error:
        logger.exception("Meeting briefing synthesis failed for meeting %s", meeting_id)
        raise HTTPException(status_code=502, detail="Could not generate meeting briefing") from error

    if not briefing_path.is_file():
        raise HTTPException(status_code=500, detail="Generated briefing file is unavailable")
    return FileResponse(
        path=briefing_path,
        media_type="audio/mpeg",
        filename=briefing_path.name,
    )
