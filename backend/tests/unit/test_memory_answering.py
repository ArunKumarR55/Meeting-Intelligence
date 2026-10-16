from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from app.schemas.domain import MemoryAnswerModel
from app.services.corporate_memory import answering


def test_answerer_accepts_only_ids_from_retrieved_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answering.settings, "gemini_api_key", SecretStr("test-key"))
    client = Mock()
    client.create.return_value = MemoryAnswerModel(
        answer="The launch was planned for June.",
        source_ids=["chunk-1"],
    )
    monkeypatch.setattr(answering, "INSTRUCTOR_CLIENT", client)
    sources = [
        {
            "id": "chunk-1",
            "meeting_id": 1,
            "chunk_index": 0,
            "transcript_chunk": "The team planned the launch for June.",
        }
    ]

    result = answering.answer_memory_query("When is launch planned?", sources)

    assert result.source_ids == ["chunk-1"]
    serialized_context = client.create.call_args.kwargs["messages"][1]["content"]
    assert "The team planned the launch for June." in serialized_context


def test_answerer_rejects_fabricated_citation_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answering.settings, "gemini_api_key", SecretStr("test-key"))
    client = Mock()
    client.create.return_value = MemoryAnswerModel(
        answer="The team chose June.",
        source_ids=["not-retrieved"],
    )
    monkeypatch.setattr(answering, "INSTRUCTOR_CLIENT", client)

    with pytest.raises(ValueError, match="not retrieved"):
        answering.answer_memory_query(
            "When?",
            [{"id": "chunk-1", "transcript_chunk": "The team chose June."}],
        )
