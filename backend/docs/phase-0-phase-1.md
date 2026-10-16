# Phase 0 and Phase 1: reliability foundation and diarization

## Phase 0 scope

- Keep SQLite schema changes additive and versioned through Alembic.
- Retry transient Gemini provider responses at most three times with short bounded delays.
- Keep provider logs limited to status codes and exception class names; transcript text and credentials are not logged.
- Return HTTP 503 when the configured intelligence provider remains unavailable after retries.
- Run focused regression tests with `pip install -r requirements-dev.txt` and `pytest` from `backend/`.

The API startup keeps `Base.metadata.create_all` for first-run installations and applies the Alembic head revision afterward. The initial revision is safe for existing databases and adds only the diarization columns to `meetings`.

## Phase 1 scope

- Transcribe locally with faster-whisper and retain redacted, timestamped transcript segments.
- Run pyannote locally when a Hugging Face token has access to the gated `pyannote/speaker-diarization-community-1` model.
- Align speaker turns to transcript segments by maximum time overlap.
- Persist the diarization status and show speaker labels and timestamps in the meeting transcript view.
- Continue uploads when diarization is not configured or unavailable; the UI reports that state instead of presenting unlabeled speech as identified speakers.
- If a sensitive value crosses ASR segment boundaries, retain only the globally redacted transcript as an unlabeled segment.

## Hugging Face model access

The pyannote model is gated. Each developer must accept the model conditions on Hugging Face, create a read token, and place it in `backend/.env` as `HF_TOKEN=...`. Never commit the token. Model weights are cached locally in `data/models/pyannote/`; the audio itself is processed on the user's machine and is not uploaded to Hugging Face by this application.

Without an accepted token, the transcript and analytics continue to work and the meeting records `diarization_status` as `not_configured`. A model download or inference error records `unavailable` and does not fail the meeting upload.
