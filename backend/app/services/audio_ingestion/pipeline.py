"""Local speech transcription with cross-platform hardware selection."""

import logging
from pathlib import Path
from threading import Lock

import torch
from faster_whisper import WhisperModel

from app.core.config import PROJECT_ROOT
from app.schemas.domain import AudioProcessingResult, TranscriptSegment
from app.services.audio_ingestion.diarization import (
    SpeakerTurn,
    assign_speakers,
    diarize_audio,
)
from app.services.audio_ingestion.pii_redactor import redact_pii


WHISPER_MODEL_SIZE = "small"
WHISPER_MODEL_CACHE = PROJECT_ROOT / "data" / "models" / "faster-whisper"
logger = logging.getLogger(__name__)


def _select_hardware() -> tuple[str, str]:
    """Return the CTranslate2 device and compute precision for this host."""
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "float32"

    # faster-whisper uses CTranslate2, whose prebuilt backend supports CPU and
    # NVIDIA CUDA. PyTorch MPS is detected for the requested platform check, but
    # transcription must run on CPU on Apple Silicon with this backend.
    ctranslate2_device = "cpu" if device == "mps" else device
    return ctranslate2_device, compute_type


_MODEL_LOCK = Lock()
_TRANSCRIPTION_LOCK = Lock()
_CACHED_MODEL: WhisperModel | None = None


def _load_model() -> WhisperModel:
    """Load and cache one local faster-whisper model for this process."""
    global _CACHED_MODEL
    with _MODEL_LOCK:
        if _CACHED_MODEL is None:
            WHISPER_MODEL_CACHE.mkdir(parents=True, exist_ok=True)
            ctranslate2_device, compute_type = _select_hardware()
            _CACHED_MODEL = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=ctranslate2_device,
                compute_type=compute_type,
                download_root=str(WHISPER_MODEL_CACHE),
            )
        return _CACHED_MODEL


def _resolve_audio_path(file_path: Path) -> Path:
    if not isinstance(file_path, Path):
        raise TypeError("file_path must be a pathlib.Path")
    try:
        audio_path = file_path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Audio file does not exist: {file_path}") from error

    if not audio_path.is_file():
        raise ValueError(f"Audio path is not a regular file: {audio_path}")
    return audio_path


def _transcribe_audio(
    file_path: Path,
) -> tuple[str, list[TranscriptSegment], float, bool]:
    """Transcribe locally and return redacted full text and time-aligned segments."""
    audio_path = _resolve_audio_path(file_path)
    model = _load_model()
    with _TRANSCRIPTION_LOCK:
        raw_segments, info = model.transcribe(
            str(audio_path),
            task="transcribe",
            beam_size=5,
            vad_filter=True,
        )
        segments = [
            TranscriptSegment(
                start_seconds=max(0.0, float(segment.start)),
                end_seconds=float(segment.end),
                speaker_id=None,
                text=redact_pii(segment.text.strip()),
            )
            for segment in raw_segments
            if segment.text and segment.text.strip() and float(segment.end) > float(segment.start)
        ]
        duration_seconds = max(0.0, float(getattr(info, "duration", 0.0) or 0.0))

    redacted_transcript = redact_pii(" ".join(segment.text for segment in segments)).strip()
    # A sensitive value can span ASR segment boundaries. In that case, storing
    # individually redacted segments could retain part of the original value.
    if " ".join(segment.text for segment in segments).strip() != redacted_transcript:
        segments = (
            [
                TranscriptSegment(
                    start_seconds=0.0,
                    end_seconds=max(duration_seconds, 0.001),
                    speaker_id=None,
                    text=redacted_transcript,
                )
            ]
            if redacted_transcript
            else []
        )
        return redacted_transcript, segments, duration_seconds, True
    return redacted_transcript, segments, duration_seconds, False


def process_audio(file_path: Path) -> str:
    """Transcribe one local audio file and redact PII before returning text."""
    transcript, _segments, _duration, _privacy_fallback = _transcribe_audio(file_path)
    return transcript


def process_audio_with_diarization(file_path: Path) -> AudioProcessingResult:
    """Transcribe and redact locally, then optionally assign local speaker labels.

    If model access or inference is unavailable, the upload still succeeds with
    unlabeled transcript segments and a truthful status for the UI.
    """
    transcript, segments, duration_seconds, privacy_fallback = _transcribe_audio(file_path)
    if privacy_fallback:
        return AudioProcessingResult(
            transcript=transcript,
            transcript_segments=segments,
            duration_seconds=duration_seconds,
            diarization_status="privacy_fallback",
        )
    if not transcript:
        return AudioProcessingResult(
            transcript="",
            transcript_segments=segments,
            duration_seconds=duration_seconds,
            diarization_status="not_configured",
        )

    from app.services.audio_ingestion.diarization import _configured_huggingface_token

    if _configured_huggingface_token() is None:
        return AudioProcessingResult(
            transcript=transcript,
            transcript_segments=segments,
            duration_seconds=duration_seconds,
            diarization_status="not_configured",
        )

    try:
        turns: list[SpeakerTurn] = diarize_audio(_resolve_audio_path(file_path))
        aligned_segments = assign_speakers(segments, turns)
        return AudioProcessingResult(
            transcript=transcript,
            transcript_segments=aligned_segments,
            duration_seconds=duration_seconds,
            diarization_status="completed",
        )
    except Exception as error:
        logger.warning("Local speaker diarization unavailable (%s)", type(error).__name__)
        return AudioProcessingResult(
            transcript=transcript,
            transcript_segments=segments,
            duration_seconds=duration_seconds,
            diarization_status="unavailable",
        )
