"""Cross-platform system-output audio capture for a live meeting session."""

from __future__ import annotations

import logging
import platform
import threading
from pathlib import Path
from time import monotonic

from app.core.config import settings


logger = logging.getLogger(__name__)
SAMPLE_RATE = 16_000
CHUNK_SECONDS = 2


class AudioCaptureError(RuntimeError):
    """Raised when the local machine cannot provide a usable loopback source."""


class SystemAudioCapture:
    """Record browser/system playback to a mono PCM WAV file on this machine.

    Windows uses SoundCard's WASAPI loopback microphone for the default output.
    macOS requires BlackHole (or a compatible virtual loopback input) and uses
    the configured ``MEETING_AUDIO_DEVICE`` input. The browser microphone is
    never captured by this component.
    """

    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self._started_at: float | None = None

    def start(self, ready_timeout: float = 15.0) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._record,
            name="amip-system-audio-capture",
            daemon=True,
        )
        self._thread.start()
        if not self._ready_event.wait(ready_timeout):
            self._stop_event.set()
            self._thread.join(timeout=3)
            raise AudioCaptureError(
                "Audio capture did not initialize. Check the local audio device and permissions."
            )
        if self._error is not None:
            raise self._public_error(self._error)

    def stop(self, join_timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                raise AudioCaptureError("Audio capture did not stop cleanly.")
        if self._error is not None:
            raise self._public_error(self._error)

    @property
    def duration_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, monotonic() - self._started_at)

    @staticmethod
    def _public_error(error: Exception) -> AudioCaptureError:
        if isinstance(error, AudioCaptureError):
            return error
        return AudioCaptureError(
            f"System audio capture failed ({type(error).__name__}). Check the local audio setup."
        )

    def _select_microphone(self, soundcard_module: object) -> object:
        system = platform.system()
        if system == "Windows":
            speaker = soundcard_module.default_speaker()
            if speaker is None:
                raise AudioCaptureError("Windows has no active default audio output device.")
            microphone = soundcard_module.get_microphone(
                id=str(speaker.name),
                include_loopback=True,
            )
            if microphone is None:
                raise AudioCaptureError(
                    "Could not open Windows audio loopback. Check the default playback device."
                )
            return microphone

        if system == "Darwin":
            configured_name = settings.meeting_audio_device.strip()
            microphones = soundcard_module.all_microphones(include_loopback=False)
            microphone = next(
                (
                    item
                    for item in microphones
                    if configured_name.casefold() in str(item.name).casefold()
                ),
                None,
            )
            if microphone is None:
                raise AudioCaptureError(
                    f"The configured macOS loopback input '{configured_name}' was not found. "
                    "Install and configure BlackHole, then restart AMIP."
                )
            return microphone

        raise AudioCaptureError(
            f"Live audio capture is not configured for {system or 'this operating system'}."
        )

    def _record(self) -> None:
        try:
            import soundcard
            import soundfile

            microphone = self._select_microphone(soundcard)
            with microphone.recorder(samplerate=SAMPLE_RATE, channels=1) as recorder:
                with soundfile.SoundFile(
                    self.destination,
                    mode="w",
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    subtype="PCM_16",
                    format="WAV",
                ) as output:
                    self._started_at = monotonic()
                    self._ready_event.set()
                    while not self._stop_event.is_set():
                        frames = recorder.record(numframes=SAMPLE_RATE * CHUNK_SECONDS)
                        if frames is not None and len(frames):
                            output.write(frames)
        except Exception as error:
            self._error = error
            self._ready_event.set()
            if not isinstance(error, AudioCaptureError):
                logger.warning("Live system audio capture failed (%s)", type(error).__name__)
