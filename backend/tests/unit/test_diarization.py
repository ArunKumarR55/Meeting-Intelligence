from app.schemas.domain import TranscriptSegment
from app.services.audio_ingestion.diarization import SpeakerTurn, assign_speakers


def test_assigns_speaker_with_greatest_overlap_and_leaves_gaps_unknown() -> None:
    segments = [
        TranscriptSegment(start_seconds=0, end_seconds=4, text="First segment"),
        TranscriptSegment(start_seconds=8, end_seconds=9, text="No matching turn"),
    ]
    turns = [
        SpeakerTurn(start_seconds=0, end_seconds=3, speaker_id="SPEAKER_00"),
        SpeakerTurn(start_seconds=2, end_seconds=4, speaker_id="SPEAKER_01"),
    ]

    result = assign_speakers(segments, turns)

    assert result[0].speaker_id == "SPEAKER_00"
    assert result[1].speaker_id is None
    assert result[0].text == "First segment"
