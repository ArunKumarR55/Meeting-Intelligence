# Phase 0 and Phase 1: reliability foundation and diarization

## Phase 0 scope

- Keep SQLite schema changes additive and versioned through Alembic.
- Retry transient Gemini provider responses at most three times with short bounded delays.
- Keep provider logs limited to status codes and exception class names; transcript text and credentials are not logged.
- Return HTTP 503 when the configured intelligence provider remains unavailable after retries.
- Run focused regression tests with `pip install -r requirements-dev.txt` and `pytest` from `backend/`.

The API startup keeps `Base.metadata.create_all` for first-run installations and applies the Alembic head revision afterward. The initial revision is safe for existing databases and adds only the diarization columns to `meetings`.

## Phase 1 scope

- Send uploaded audio to AssemblyAI's prerecorded transcription API with speaker labels enabled.
- Retain timestamped utterances and stable AMIP speaker IDs returned by AssemblyAI.
- Redact detected PII locally before sending the transcript to Gemini or indexing it in ChromaDB.
- Persist the diarization status and show speaker labels and timestamps in the meeting transcript view.
- Continue uploads when diarization is not configured or unavailable; the UI reports that state instead of presenting unlabeled speech as identified speakers.
- If a sensitive value crosses ASR segment boundaries, retain only the globally redacted transcript as an unlabeled segment.

Uploaded audio is transferred to AssemblyAI for transcription and speaker labeling. Configure `ASSEMBLYAI_API_KEY` in `backend/.env`. The API key remains on the backend and is not sent to the browser. If AssemblyAI returns no speaker labels, transcription and meeting analysis continue with unlabeled segments.
