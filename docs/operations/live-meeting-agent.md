# Local live meeting assistant

The live assistant is an optional input path. It opens Google Meet or Microsoft Teams in a visible local Chromium window, uses the participant name **AMIP Meeting Assistant**, keeps its own microphone and camera off, and starts system-output capture only after it has entered the call. Sign-in, CAPTCHA, lobby approval, and host admission remain under the user's control. The operator must have permission to record and notify meeting participants.

When the operator stops a session, AMIP sends its WAV through the same `/api/v1/audio/upload` implementation used by manual uploads. Whisper, local redaction, optional diarization, extraction, persistence, RAG indexing, and selected MOM email recipients therefore follow the existing path. If capture succeeds but that pipeline fails, the dashboard offers the WAV for download and manual upload. If the local capture device or browser cannot start, manual upload remains available as usual.

## Install the browser runtime

After installing backend requirements, install Playwright Chromium from the backend virtual environment:

```text
python -m playwright install chromium
```

The backend must run in the signed-in desktop user's session because Playwright opens a visible browser window and captures audio from that computer. A headless/server deployment without a desktop audio session is not supported by this local-capture mode.

## macOS audio routing

1. Install the BlackHole 2-channel virtual audio driver from the [BlackHole project](https://github.com/ExistentialAudio/BlackHole). Its documented Homebrew install command is `brew install blackhole-2ch`.
2. In **Audio MIDI Setup**, create a Multi-Output Device containing BlackHole 2ch and the speakers/headphones where meeting audio should be heard.
3. Select that Multi-Output Device as the macOS output while recording. This routes system playback to BlackHole and to the selected listening device.
4. Set `MEETING_AUDIO_DEVICE=BlackHole 2ch` in `backend/.env` and restart the backend.
5. Grant macOS audio-device access if prompted. Keep the output route on the BlackHole Multi-Output Device for the full recording.

Do not play unrelated audio during a live session: macOS loopback records the selected system output.

## Windows audio routing

SoundCard captures the default Windows playback endpoint through WASAPI loopback. Select the speakers/headphones used for the meeting as the Windows default output before starting the session. `MEETING_AUDIO_DEVICE` is used on macOS and can be left unchanged on Windows. If the machine's audio driver does not expose WASAPI loopback, configure a virtual cable as the default playback endpoint and choose a monitoring route so the operator can still hear the meeting.

## Configuration

```dotenv
MEETING_AGENT_ENABLED=true
MEETING_AGENT_NAME=AMIP Meeting Assistant
MEETING_AUDIO_DEVICE=BlackHole 2ch
MEETING_AGENT_BROWSER_HEADLESS=false
```

The accepted meeting URLs must use HTTPS and a Google Meet or Microsoft Teams host. Start a session from **New meeting → Join a live meeting**, select the MOM recipients, confirm recording permission, and open the assistant. Complete any login or admission steps in its browser window. Capture begins only once the call's leave control is visible. Stop the session from the dashboard to create and process the WAV.

## Session limits

- One live session can run per backend process at a time. Session state is held in process memory; restarting the backend ends that session.
- The Playwright browser and capture device must run on the same local computer as the backend.
- Provider-specific page changes can require selector updates. The user can complete visible meeting UI steps; AMIP does not bypass account sign-in, access controls, CAPTCHA, or host admission.
- Manual audio upload is independent and remains available if live meeting setup or processing fails.
