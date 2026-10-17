from io import BytesIO

from fastapi import UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.db.models.domain import Meeting
from app.schemas.domain import AudioProcessingResult, MeetingIntelligenceModel, TranscriptSegment
from app.api.v1.endpoints import audio as audio_endpoint
from app.services import meeting_processing


def test_upload_persists_redacted_segments_and_intelligence(monkeypatch, tmp_path) -> None:
    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    session = Session(test_engine, expire_on_commit=False)
    monkeypatch.setattr(audio_endpoint, "UPLOAD_DIRECTORY", tmp_path / "uploads")
    monkeypatch.setattr(audio_endpoint.settings, "assemblyai_api_key", "test-assemblyai-key")

    def transcribe(path):
        assert path.exists()
        return AudioProcessingResult(
            transcript="Planning starts tomorrow.",
            transcript_segments=[
                TranscriptSegment(
                    start_seconds=0.0,
                    end_seconds=2.0,
                    speaker_id="SPEAKER_00",
                    text="Planning starts tomorrow.",
                )
            ],
            duration_seconds=2.0,
            diarization_status="completed",
        )

    monkeypatch.setattr(audio_endpoint, "transcribe_recording", transcribe)
    monkeypatch.setattr(
        meeting_processing,
        "extract_meeting_intelligence",
        lambda _transcript, meeting_date: MeetingIntelligenceModel(
            executive_summary="The team planned the next steps.",
            key_topics=["Planning"],
            timeline=[],
            action_items=[],
            decisions=[],
            risks=[],
        ),
    )
    monkeypatch.setattr(meeting_processing, "calculate_participation_metrics", lambda _text: {"word_count": 3})
    monkeypatch.setattr(
        meeting_processing,
        "index_meeting_transcript",
        lambda _meeting_id, _text, transcript_segments: None,
    )
    monkeypatch.setattr(meeting_processing, "track_action_item_drift", lambda _db, _meeting_id: None)

    try:
        upload = UploadFile(filename="meeting.wav", file=BytesIO(b"audio-bytes"))
        result = audio_endpoint.upload_audio(file=upload, title="Planning", db=session)

        meeting = session.scalar(select(Meeting).where(Meeting.id == result["meeting_id"]))
        assert meeting is not None
        assert meeting.transcript == "Planning starts tomorrow."
        assert meeting.transcript_segments[0]["speaker_id"] == "SPEAKER_00"
        assert meeting.diarization_status == "completed"
        assert result["transcript_segment_count"] == 1
    finally:
        session.close()
        test_engine.dispose()

