from pathlib import Path

from app.schemas.domain import TranscriptSegment
from app.services.audio_ingestion import pipeline


def test_diarization_is_skipped_when_token_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "_transcribe_audio",
        lambda _path: (
            "The release is ready.",
            [TranscriptSegment(start_seconds=0, end_seconds=1, text="The release is ready.")],
            1.0,
            False,
        ),
    )
    monkeypatch.setattr(
        "app.services.audio_ingestion.diarization._configured_huggingface_token",
        lambda: None,
    )

    result = pipeline.process_audio_with_diarization(Path("unused.wav"))

    assert result.transcript == "The release is ready."
    assert result.diarization_status == "not_configured"
    assert result.transcript_segments[0].speaker_id is None


def test_cross_segment_pii_fallback_does_not_run_diarization(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "_transcribe_audio",
        lambda _path: (
            "Call [REDACTED_PHONE]",
            [TranscriptSegment(start_seconds=0, end_seconds=2, text="Call [REDACTED_PHONE]")],
            2.0,
            True,
        ),
    )
    monkeypatch.setattr(
        "app.services.audio_ingestion.diarization._configured_huggingface_token",
        lambda: "configured-token",
    )
    monkeypatch.setattr(
        pipeline,
        "diarize_audio",
        lambda _path: (_ for _ in ()).throw(AssertionError("diarization must be skipped")),
    )

    result = pipeline.process_audio_with_diarization(Path("unused.wav"))

    assert result.diarization_status == "privacy_fallback"
    assert result.transcript_segments[0].speaker_id is None
