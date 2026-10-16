from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import rag as rag_endpoint
from app.core.database import Base, get_db
from app.db.models.domain import Meeting, UnsubscribedEmail
from app.main import app
from app.schemas.domain import MemoryAnswerModel


def test_rag_answer_and_unsubscribe_routes_over_http(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as setup_session:
        meeting = Meeting(
            title="Launch planning",
            transcript="The team discussed the launch date.",
            transcript_segments=[
                {"text": "The team discussed the launch date.", "start_seconds": 34.0, "end_seconds": 39.0}
            ],
            executive_summary="The team reviewed launch timing.",
        )
        setup_session.add(meeting)
        setup_session.commit()
        meeting_id = meeting.id

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        rag_endpoint,
        "query_memory",
        lambda *_args, **_kwargs: [
            {
                "id": "meeting-1-chunk-0-test",
                "meeting_id": meeting_id,
                "chunk_index": 0,
                "character_start": 0,
                "character_end": 36,
                "start_seconds": None,
                "end_seconds": None,
                "transcript_chunk": "The team discussed the launch date.",
            }
        ],
    )
    monkeypatch.setattr(
        rag_endpoint,
        "answer_memory_query",
        lambda *_args, **_kwargs: MemoryAnswerModel(
            answer="The team discussed the launch date.",
            source_ids=["meeting-1-chunk-0-test"],
        ),
    )

    try:
        client = TestClient(app)
        answer_response = client.post(
            "/api/v1/rag/query",
            json={"query": "What did the team discuss?"},
        )
        unsubscribe_response = client.post(
            "/api/v1/workflows/unsubscribe?email=person%40example.test",
            data={"List-Unsubscribe": "One-Click"},
        )
        confirmation_response = client.get(
            "/api/v1/workflows/unsubscribe?email=PERSON%40example.test"
        )

        assert answer_response.status_code == 200
        answer_payload = answer_response.json()
        assert answer_payload["answer"] == "The team discussed the launch date."
        assert answer_payload["citations"][0]["timestamp_label"] == "00:34"
        assert unsubscribe_response.status_code == 204
        assert confirmation_response.status_code == 200
        assert "has been unsubscribed" in confirmation_response.text
        with Session(engine) as verify_session:
            preference = verify_session.scalar(select(UnsubscribedEmail))
            assert preference is not None
            assert preference.email_normalized == "person@example.test"
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
