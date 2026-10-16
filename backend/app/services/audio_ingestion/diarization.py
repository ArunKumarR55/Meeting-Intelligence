"""Local pyannote speaker diarization and ASR segment alignment."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import torch

from app.core.config import PROJECT_ROOT, settings
from app.schemas.domain import TranscriptSegment


logger = logging.getLogger(__name__)
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
DIARIZATION_CACHE = PROJECT_ROOT / "data" / "models" / "pyannote"
_PIPELINE_LOCK = Lock()
_INFERENCE_LOCK = Lock()
_CACHED_PIPELINE = None


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """A single speaker turn emitted by pyannote."""

    start_seconds: float
    end_seconds: float
    speaker_id: str


def _select_device() -> str:
    """Select an available PyTorch accelerator on macOS, Windows, or CPU."""
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    return device


def _configured_huggingface_token() -> str | None:
    token = settings.huggingface_token
    if token is None:
        return None
    value = token.get_secret_value().strip()
    if not value or value.casefold().startswith("replace-with"):
        return None
    return value


def _load_pipeline():
    """Load and cache the open-source diarization pipeline locally."""
    global _CACHED_PIPELINE
    with _PIPELINE_LOCK:
        if _CACHED_PIPELINE is None:
            from pyannote.audio import Pipeline

            DIARIZATION_CACHE.mkdir(parents=True, exist_ok=True)
            token = _configured_huggingface_token()
            pipeline = Pipeline.from_pretrained(
                DIARIZATION_MODEL,
                token=token,
                cache_dir=DIARIZATION_CACHE,
            )
            if pipeline is None:
                raise RuntimeError("pyannote did not load a diarization pipeline")
            pipeline.to(torch.device(_select_device()))
            _CACHED_PIPELINE = pipeline
        return _CACHED_PIPELINE


def diarize_audio(file_path: Path) -> list[SpeakerTurn]:
    """Run local pyannote diarization for an audio file and return speaker turns.

    The pyannote model weights are cached under the repository data directory.
    The Hugging Face token is used only for model access and is never persisted
    with meeting data or included in log messages.
    """
    if not isinstance(file_path, Path):
        raise TypeError("file_path must be a pathlib.Path")
    audio_path = file_path.expanduser().resolve(strict=True)
    if not audio_path.is_file():
        raise ValueError("audio path must refer to a regular file")

    pipeline = _load_pipeline()
    with _INFERENCE_LOCK:
        output = pipeline(str(audio_path))

    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", None)
    if annotation is None:
        raise RuntimeError("pyannote returned no speaker diarization")

    turns: list[SpeakerTurn] = []
    for time_segment, _track, speaker_id in annotation.itertracks(yield_label=True):
        start_seconds = float(time_segment.start)
        end_seconds = float(time_segment.end)
        if end_seconds <= start_seconds:
            continue
        turns.append(
            SpeakerTurn(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                speaker_id=str(speaker_id),
            )
        )
    return turns


def assign_speakers(
    transcript_segments: list[TranscriptSegment],
    speaker_turns: list[SpeakerTurn],
) -> list[TranscriptSegment]:
    """Assign each ASR segment the speaker with the greatest time overlap."""
    aligned_segments: list[TranscriptSegment] = []
    for transcript_segment in transcript_segments:
        overlap_by_speaker: dict[str, float] = {}
        for turn in speaker_turns:
            overlap = min(transcript_segment.end_seconds, turn.end_seconds) - max(
                transcript_segment.start_seconds,
                turn.start_seconds,
            )
            if overlap > 0:
                overlap_by_speaker[turn.speaker_id] = (
                    overlap_by_speaker.get(turn.speaker_id, 0.0) + overlap
                )
        speaker_id = (
            max(overlap_by_speaker, key=overlap_by_speaker.get)
            if overlap_by_speaker
            else None
        )
        aligned_segments.append(
            transcript_segment.model_copy(update={"speaker_id": speaker_id})
        )
    return aligned_segments
