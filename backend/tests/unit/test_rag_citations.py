from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.db.models.domain import Meeting
from app.schemas.domain import MemoryAnswerModel
from app.api.v1.endpoints import rag as rag_endpoint
from app.api.v1.endpoints.rag import MemoryQuery, _format_timestamp, _segment_timestamps_for_chunk


def test_chunk_timestamps_are_reconstructed_from_asr_segment_offsets() -> None:
    segments = [
        {"text": "Launch", "start_seconds": 12.0, "end_seconds": 14.0},
        {"text": "plan", "start_seconds": 14.0, "end_seconds": 18.0},
    ]

    start, end = _segment_timestamps_for_chunk(segments, 4, 10)

    assert (start, end) == (12.0, 18.0)
    assert _format_timestamp(3_661) == "1:01:01"


def test_memory_query_returns_grounded_citation_with_recording_time(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        meeting = Meeting(
            title="Planning session",
            transcript="Launch plan",
            transcript_segments=[
                {"text": "Launch", "start_seconds": 12.0, "end_seconds": 14.0},
                {"text": "plan", "start_seconds": 14.0, "end_seconds": 18.0},
            ],
        )
        db.add(meeting)
        db.flush()
        monkeypatch.setattr(
            rag_endpoint,
            "query_memory",
            lambda *_args, **_kwargs: [
                {
                    "id": "chunk-1",
                    "meeting_id": meeting.id,
                    "chunk_index": 0,
                    "character_start": 0,
                    "character_end": 11,
                    "start_seconds": None,
                    "end_seconds": None,
                    "transcript_chunk": "Launch plan",
                }
            ],
        )
        monkeypatch.setattr(
            rag_endpoint,
            "answer_memory_query",
            lambda *_args, **_kwargs: MemoryAnswerModel(
                answer="The team discussed the launch plan.",
                source_ids=["chunk-1"],
            ),
        )

        response = rag_endpoint.search_meeting_memory(MemoryQuery(query="What was discussed?"), db)

    citation = response["citations"][0]
    assert citation["meeting_title"] == "Planning session"
    assert citation["timestamp_label"] == "00:12"
    assert citation["source_id"] == "chunk-1"
    engine.dispose()
