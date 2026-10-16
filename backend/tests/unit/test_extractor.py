from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from app.schemas.domain import MeetingIntelligenceModel
from app.services.analytics import extractor


def valid_result() -> MeetingIntelligenceModel:
    return MeetingIntelligenceModel(
        executive_summary="The team reviewed project status.",
        key_topics=[],
        timeline=[],
        action_items=[],
        decisions=[],
        risks=[],
    )


def test_transient_provider_error_retries_then_returns_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractor.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(extractor.time, "sleep", lambda _seconds: None)
    client = Mock()
    transient_error = RuntimeError("provider busy")
    transient_error.status_code = 503
    client.create.side_effect = [transient_error, valid_result()]
    monkeypatch.setattr(extractor, "INSTRUCTOR_CLIENT", client)

    result = extractor.extract_meeting_intelligence("We reviewed project status.")

    assert isinstance(result, MeetingIntelligenceModel)
    assert client.create.call_count == 2
    assert "temperature" not in client.create.call_args.kwargs


def test_provider_unavailable_after_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractor.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(extractor.time, "sleep", lambda _seconds: None)
    client = Mock()
    transient_error = RuntimeError("provider busy")
    transient_error.status_code = 503
    client.create.side_effect = transient_error
    monkeypatch.setattr(extractor, "INSTRUCTOR_CLIENT", client)

    with pytest.raises(extractor.LLMProviderUnavailableError):
        extractor.extract_meeting_intelligence("We reviewed project status.")

    assert client.create.call_count == extractor.MAX_EXTRACTION_ATTEMPTS
