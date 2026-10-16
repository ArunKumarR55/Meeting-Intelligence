import { useEffect, useRef, useState } from "react";
import { addMomRecipient, deleteMomRecipient, getActiveLiveMeetingStatus, getLiveMeetingStatus, getMomRecipients, startLiveMeeting, stopLiveMeeting, uploadMeeting } from "../lib/api";

const ACCEPTED_TYPES = new Set(["audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav"]);
const MAX_FILE_BYTES = 500 * 1024 * 1024;

export default function AudioUploader({ onUploaded }) {
  const inputRef = useRef(null);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [recipients, setRecipients] = useState([]);
  const [selectedEmails, setSelectedEmails] = useState([]);
  const [newRecipientEmail, setNewRecipientEmail] = useState("");
  const [newRecipientName, setNewRecipientName] = useState("");
  const [recipientBusy, setRecipientBusy] = useState(false);
  const [recipientError, setRecipientError] = useState("");
  const [mode, setMode] = useState("upload");
  const [meetingUrl, setMeetingUrl] = useState("");
  const [recordingConsent, setRecordingConsent] = useState(false);
  const [liveSession, setLiveSession] = useState(null);
  const [liveSubmitting, setLiveSubmitting] = useState(false);
  const [liveError, setLiveError] = useState("");
  const onUploadedRef = useRef(onUploaded);
  const processedSessionRef = useRef(null);

  useEffect(() => { onUploadedRef.current = onUploaded; }, [onUploaded]);

  useEffect(() => {
    let active = true;
    getMomRecipients()
      .then((savedRecipients) => {
        if (active) setRecipients(Array.isArray(savedRecipients) ? savedRecipients : []);
      })
      .catch((loadError) => {
        if (active) setRecipientError(loadError.message || "Could not load saved email recipients.");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    getActiveLiveMeetingStatus()
      .then((session) => {
        if (!active || !session) return;
        setLiveSession(session);
        setMode("live");
        setTitle(session.title || "");
        setRecordingConsent(true);
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const sessionId = liveSession?.session_id;
    if (!sessionId || !["starting", "waiting_for_user", "waiting_for_admission", "recording", "stopping", "processing"].includes(liveSession.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const updated = await getLiveMeetingStatus(sessionId);
        setLiveSession(updated);
        if (updated.status === "completed" && processedSessionRef.current !== sessionId) {
          processedSessionRef.current = sessionId;
          await onUploadedRef.current?.({ meeting_id: updated.meeting_id });
        }
      } catch (statusError) {
        setLiveError(statusError.message || "Could not refresh the live meeting status.");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [liveSession?.session_id, liveSession?.status]);

  async function saveRecipient() {
    setRecipientBusy(true);
    setRecipientError("");
    try {
      const saved = await addMomRecipient(newRecipientEmail, newRecipientName);
      setRecipients((current) => [...current, saved].sort((left, right) =>
        (left.display_name || left.email).localeCompare(right.display_name || right.email),
      ));
      setSelectedEmails((current) => [...new Set([...current, saved.email])]);
      setNewRecipientEmail("");
      setNewRecipientName("");
    } catch (saveError) {
      setRecipientError(saveError.message || "Could not save this email recipient.");
    } finally {
      setRecipientBusy(false);
    }
  }

  async function removeRecipient(recipient) {
    setRecipientBusy(true);
    setRecipientError("");
    try {
      await deleteMomRecipient(recipient.id);
      setRecipients((current) => current.filter((item) => item.id !== recipient.id));
      setSelectedEmails((current) => current.filter((email) => email !== recipient.email));
    } catch (removeError) {
      setRecipientError(removeError.message || "Could not remove this saved recipient.");
    } finally {
      setRecipientBusy(false);
    }
  }

  function toggleRecipient(email) {
    setSelectedEmails((current) => current.includes(email)
      ? current.filter((value) => value !== email)
      : [...current, email]);
  }

  function validateFile(candidate) {
    if (!candidate) return "Choose an audio file to continue.";
    const extension = candidate.name.split(".").pop()?.toLowerCase();
    if (!new Set(["mp3", "wav"]).has(extension)) {
      return "Select an MP3 or WAV audio file.";
    }
    if (candidate.type && !ACCEPTED_TYPES.has(candidate.type.toLowerCase())) {
      return "The selected file must be an MP3 or WAV recording.";
    }
    if (candidate.size > MAX_FILE_BYTES) {
      return "The selected file exceeds the 500 MiB upload limit.";
    }
    if (candidate.size === 0) return "The selected audio file is empty.";
    return "";
  }

  function chooseFile(candidate) {
    const fileError = validateFile(candidate);
    setError(fileError);
    setFile(fileError ? null : candidate);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const normalizedTitle = title.trim();
    if (!normalizedTitle) {
      setError("Add a meeting title before uploading.");
      return;
    }
    const fileError = validateFile(file);
    if (fileError) {
      setError(fileError);
      return;
    }

    setError("");
    setLoading(true);
    const formData = new FormData();
    formData.append("title", normalizedTitle);
    formData.append("file", file);
    formData.append("recipients", JSON.stringify(selectedEmails));
    try {
      const result = await uploadMeeting(formData);
      setTitle("");
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await onUploaded?.(result);
    } catch (uploadError) {
      setError(uploadError.message || "The meeting could not be processed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleStartLiveMeeting(event) {
    event.preventDefault();
    if (!title.trim()) {
      setLiveError("Add a meeting title before starting the live assistant.");
      return;
    }
    if (!meetingUrl.trim()) {
      setLiveError("Paste a Google Meet or Microsoft Teams meeting link.");
      return;
    }
    if (!recordingConsent) {
      setLiveError("Confirm recording permission and participant notification to continue.");
      return;
    }
    setLiveSubmitting(true);
    setLiveError("");
    try {
      const started = await startLiveMeeting(meetingUrl, title, selectedEmails, recordingConsent);
      processedSessionRef.current = null;
      setLiveSession(started);
    } catch (startError) {
      setLiveError(startError.message || "Could not start the live meeting assistant.");
    } finally {
      setLiveSubmitting(false);
    }
  }

  async function handleStopLiveMeeting() {
    if (!liveSession?.session_id) return;
    setLiveError("");
    try {
      setLiveSession(await stopLiveMeeting(liveSession.session_id));
    } catch (stopError) {
      setLiveError(stopError.message || "Could not stop the live meeting assistant.");
    }
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <section className="mx-auto w-full max-w-3xl">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-indigo-50 text-indigo-600">
          <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7" aria-hidden="true">
            <path d="M12 15V3m0 0L7.5 7.5M12 3l4.5 4.5M4 14.5v3A3.5 3.5 0 007.5 21h9a3.5 3.5 0 003.5-3.5v-3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-indigo-600">Meeting intelligence</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">Turn conversations into clarity.</h1>
        <p className="mx-auto mt-3 max-w-xl text-base leading-7 text-slate-500">
          Upload a recording to get a private transcript, decisions, action items, and project risks.
        </p>
      </div>

      <form onSubmit={mode === "upload" ? handleSubmit : handleStartLiveMeeting} className="rounded-3xl border border-slate-200 bg-white p-5 shadow-[0_24px_80px_-45px_rgba(15,23,42,0.28)] sm:p-8">
        <div className="mb-6 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1" role="tablist" aria-label="Meeting input method">
          <button type="button" role="tab" aria-selected={mode === "upload"} onClick={() => setMode("upload")} disabled={Boolean(liveSession && !["completed", "failed"].includes(liveSession.status) && mode === "live")} className={`rounded-lg px-3 py-2.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${mode === "upload" ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>Upload a recording</button>
          <button type="button" role="tab" aria-selected={mode === "live"} onClick={() => setMode("live")} className={`rounded-lg px-3 py-2.5 text-xs font-semibold transition ${mode === "live" ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>Join a live meeting</button>
        </div>

        <label htmlFor="meeting-title" className="mb-2 block text-sm font-semibold text-slate-700">Meeting title</label>
        <input
          id="meeting-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          maxLength={500}
          placeholder="e.g. Product planning — Q4 launch"
          disabled={loading || liveSubmitting || Boolean(liveSession && !["completed", "failed"].includes(liveSession.status))}
          className="h-12 w-full rounded-xl border border-slate-200 bg-white px-4 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100 disabled:bg-slate-50"
        />

        <section aria-labelledby="mom-recipients-heading" className="mt-5 rounded-2xl border border-slate-200 p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 id="mom-recipients-heading" className="text-sm font-semibold text-slate-800">Meeting minutes recipients</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">Choose saved contacts to email automatically after this meeting finishes processing.</p>
            </div>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">{selectedEmails.length} selected</span>
          </div>

          {recipients.length > 0 ? (
            <ul className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-100">
              {recipients.map((recipient) => (
                <li key={recipient.id} className="flex items-center gap-3 px-3 py-2.5">
                  <input
                    id={`recipient-${recipient.id}`}
                    type="checkbox"
                    checked={selectedEmails.includes(recipient.email)}
                    onChange={() => toggleRecipient(recipient.email)}
                    disabled={loading || liveSubmitting || recipientBusy}
                    className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <label htmlFor={`recipient-${recipient.id}`} className="min-w-0 flex-1 cursor-pointer">
                    <span className="block truncate text-sm font-medium text-slate-700">{recipient.display_name || recipient.email}</span>
                    {recipient.display_name && <span className="block truncate text-xs text-slate-400">{recipient.email}</span>}
                  </label>
                  <button type="button" onClick={() => void removeRecipient(recipient)} disabled={loading || liveSubmitting || recipientBusy} className="rounded-md px-2 py-1 text-xs font-medium text-slate-400 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-50" aria-label={`Remove ${recipient.email}`}>Remove</button>
                </li>
              ))}
            </ul>
          ) : <p className="mt-3 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-500">No saved recipients yet. Add an address below or continue without email.</p>}

          <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <label className="sr-only" htmlFor="new-recipient-email">Recipient email address</label>
            <input id="new-recipient-email" type="email" maxLength={320} value={newRecipientEmail} onChange={(event) => setNewRecipientEmail(event.target.value)} placeholder="name@company.com" disabled={loading || liveSubmitting || recipientBusy} className="h-10 min-w-0 rounded-lg border border-slate-200 px-3 text-sm outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50" />
            <label className="sr-only" htmlFor="new-recipient-name">Recipient name</label>
            <input id="new-recipient-name" type="text" maxLength={120} value={newRecipientName} onChange={(event) => setNewRecipientName(event.target.value)} placeholder="Name (optional)" disabled={loading || liveSubmitting || recipientBusy} className="h-10 min-w-0 rounded-lg border border-slate-200 px-3 text-sm outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50" />
            <button type="button" onClick={() => void saveRecipient()} disabled={loading || liveSubmitting || recipientBusy || !newRecipientEmail.trim()} className="h-10 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50">{recipientBusy ? "Saving…" : "Add & select"}</button>
          </div>
          {recipientError && <p role="alert" className="mt-2 text-xs leading-5 text-rose-700">{recipientError}</p>}
        </section>

        {mode === "upload" && <><div
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={`mt-5 rounded-2xl border border-dashed p-6 text-center transition sm:p-9 ${dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-slate-50/70"}`}
        >
          <input
            ref={inputRef}
            id="audio-file"
            type="file"
            accept=".mp3,.wav,audio/mpeg,audio/wav"
            className="sr-only"
            disabled={loading}
            onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="mx-auto flex max-w-md items-center gap-3 rounded-xl border border-indigo-100 bg-white px-4 py-3 text-left">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-600" aria-hidden="true">♫</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-800">{file.name}</p>
                <p className="mt-0.5 text-xs text-slate-500">{(file.size / (1024 * 1024)).toFixed(1)} MB · Ready to process</p>
              </div>
              <button type="button" onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ""; }} disabled={loading} className="rounded-md px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100 disabled:opacity-50" aria-label="Remove selected audio">Remove</button>
            </div>
          ) : (
            <>
              <div className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-white text-slate-500 shadow-sm" aria-hidden="true">♫</div>
              <p className="mt-4 text-sm font-medium text-slate-700">Drop your recording here, or{" "}
                <label htmlFor="audio-file" className="cursor-pointer font-semibold text-indigo-600 hover:text-indigo-700">browse files</label>
              </p>
              <p className="mt-2 text-xs text-slate-500">MP3 or WAV · Up to 500 MiB</p>
            </>
          )}
        </div>

        {error && <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}

        {loading && (
          <div role="status" aria-live="polite" className="mt-5 flex items-start gap-3 rounded-xl bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
            <span className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-indigo-300 border-t-indigo-700" aria-hidden="true" />
            <span><strong className="block font-semibold">Processing your meeting</strong><span className="mt-1 block text-indigo-800">Running local Whisper transcription, then extracting meeting intelligence. This can take a few minutes.</span></span>
          </div>
        )}

        <button type="submit" disabled={loading || !file} className="mt-5 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 focus:outline-none focus:ring-4 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-slate-300">
          {loading ? "Analyzing recording…" : "Analyze meeting"}
          {!loading && <span aria-hidden="true">→</span>}
        </button>
        <p className="mt-4 text-center text-xs leading-5 text-slate-400">Audio transcription and sensitive data redaction run locally on this machine.</p>
        </>}

        {mode === "live" && <section className="mt-5 rounded-2xl border border-slate-200 p-4 sm:p-5">
          <label htmlFor="live-meeting-url" className="mb-2 block text-sm font-semibold text-slate-700">Google Meet or Microsoft Teams link</label>
          <input id="live-meeting-url" type="url" inputMode="url" autoComplete="url" maxLength={2048} value={meetingUrl} onChange={(event) => setMeetingUrl(event.target.value)} placeholder="https://meet.google.com/..." disabled={liveSubmitting || Boolean(liveSession && !["completed", "failed"].includes(liveSession.status))} className="h-12 w-full rounded-xl border border-slate-200 px-4 text-sm outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100 disabled:bg-slate-50" />
          <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-xl border border-amber-100 bg-amber-50/70 p-3.5 text-xs leading-5 text-amber-950">
            <input type="checkbox" checked={recordingConsent} onChange={(event) => setRecordingConsent(event.target.checked)} disabled={liveSubmitting || Boolean(liveSession && !["completed", "failed"].includes(liveSession.status))} className="mt-0.5 h-4 w-4 shrink-0 rounded border-amber-300 text-indigo-600 focus:ring-indigo-500" />
            <span>I have permission to record this meeting and will notify its participants. The assistant will join as <strong>{liveSession?.agent_name || "AMIP Meeting Assistant"}</strong>.</span>
          </label>
          <p className="mt-3 text-xs leading-5 text-slate-500">A local browser window opens. Sign-in and host admission stay under your control. Audio capture starts only after the assistant joins; the recording is processed by the same pipeline as a manual upload.</p>
          {liveError && <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{liveError}</p>}
          {liveSession && <div className={`mt-4 rounded-xl border p-4 ${liveSession.status === "failed" ? "border-rose-200 bg-rose-50" : liveSession.status === "completed" ? "border-emerald-200 bg-emerald-50" : "border-indigo-100 bg-indigo-50"}`} aria-live="polite">
            <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-semibold capitalize text-slate-900">{liveSession.status.replaceAll("_", " ")}</p>{liveSession.status === "recording" && <span className="flex items-center gap-1.5 text-xs font-medium text-rose-700"><span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" />Recording</span>}</div>
            <p className="mt-1.5 text-xs leading-5 text-slate-600">{liveSession.error || liveSession.message}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {["starting", "waiting_for_user", "waiting_for_admission", "recording"].includes(liveSession.status) && <button type="button" onClick={() => void handleStopLiveMeeting()} className="h-9 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white hover:bg-rose-700">Stop and process</button>}
              {liveSession.recording_url && <a href={liveSession.recording_url} className="inline-flex h-9 items-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50">Download WAV for manual upload</a>}
              {liveSession.status === "completed" && liveSession.meeting_id && <span className="self-center text-xs font-medium text-emerald-800">Meeting #{liveSession.meeting_id} is in your library.</span>}
              {["completed", "failed"].includes(liveSession.status) && <button type="button" onClick={() => { setLiveSession(null); setLiveError(""); }} className="inline-flex h-9 items-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50">Start another session</button>}
            </div>
          </div>}
          {!liveSession && <button type="submit" disabled={liveSubmitting || !title.trim() || !meetingUrl.trim() || !recordingConsent} className="mt-4 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300">{liveSubmitting ? "Starting assistant…" : "Open meeting assistant"}<span aria-hidden="true">→</span></button>}
        </section>}
      </form>
    </section>
  );
}
