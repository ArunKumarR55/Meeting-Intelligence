import { useEffect, useState } from "react";
import {
  connectCalendar,
  exportToGithub,
  getCalendarConnections,
  inferSpeakerIdentities,
  sendMomEmail,
  syncTaskToCalendar,
  updateSpeakerIdentity,
} from "../lib/api";

function formatDate(value) {
  if (!value) return "Date not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date not available"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function severityStyles(value) {
  const severity = String(value ?? "medium").toLowerCase();
  if (severity === "critical" || severity === "high") return "bg-rose-50 text-rose-700 ring-rose-600/10";
  if (severity === "medium") return "bg-amber-50 text-amber-700 ring-amber-600/10";
  return "bg-emerald-50 text-emerald-700 ring-emerald-600/10";
}

function SectionHeading({ eyebrow, title, count }) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div>
        {eyebrow && <p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-400">{eyebrow}</p>}
        <h2 className="mt-1 text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
      </div>
      {typeof count === "number" && <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500">{count}</span>}
    </div>
  );
}

function MeetingMinutesPanel({ meeting }) {
  const [recipientsText, setRecipientsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState(null);

  async function sendMinutes(event) {
    event.preventDefault();
    const recipients = recipientsText.split(/[\s,;]+/).map((address) => address.trim()).filter(Boolean);
    setBusy(true);
    setError("");
    setReport(null);
    try {
      const result = await sendMomEmail(meeting.id, recipients);
      setReport(result);
    } catch (sendError) {
      setError(sendError.message || "Could not send meeting minutes.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
      <SectionHeading eyebrow="Follow-up" title="Email meeting minutes" />
      <form onSubmit={sendMinutes} className="mt-4">
        <label htmlFor={`mom-recipients-${meeting.id}`} className="text-sm font-medium text-slate-700">Recipients</label>
        <textarea id={`mom-recipients-${meeting.id}`} required value={recipientsText} onChange={(event) => setRecipientsText(event.target.value)} placeholder="name@company.com, teammate@company.com" rows={2} maxLength={16_000} className="mt-2 w-full resize-y rounded-xl border border-slate-200 px-3 py-2.5 text-sm leading-6 outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
        <p className="mt-1 text-xs leading-5 text-slate-500">Separate addresses with commas, spaces, or new lines. Unsubscribed addresses are automatically excluded.</p>
        <button type="submit" disabled={busy || !recipientsText.trim()} className="mt-3 inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300">{busy ? "Sending minutes…" : "Send minutes"}</button>
      </form>
      {error && <p role="alert" className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-sm leading-5 text-rose-700">{error}</p>}
      {report && <div role="status" className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
        <p className="font-semibold text-slate-800">Email delivery report</p>
        <p className="mt-1 text-slate-600">Sent: {report.sent?.length ?? 0} · Unsubscribed: {report.unsubscribed?.length ?? 0} · Failed: {report.failed?.length ?? 0}</p>
        {report.unsubscribed?.length > 0 && <p className="mt-2 break-words text-xs leading-5 text-amber-800">Skipped by email preference: {report.unsubscribed.join(", ")}</p>}
        {report.failed?.length > 0 && <p className="mt-2 break-words text-xs leading-5 text-rose-700">Could not deliver to: {report.failed.join(", ")}</p>}
        {(report.sent?.length ?? 0) === 0 && (report.unsubscribed?.length ?? 0) > 0 && (report.failed?.length ?? 0) === 0 && <p className="mt-2 text-xs text-slate-600">No email was sent because every recipient has unsubscribed.</p>}
      </div>}
    </section>
  );
}

function TaskCard({ task, calendarConnections, calendarLoading, onSyncToCalendar, calendarBusy, calendarResults, calendarErrors }) {
  const [repoUrl, setRepoUrl] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [issueUrl, setIssueUrl] = useState(task.external_ticket_url ?? "");
  const completed = String(task.status ?? "").toLowerCase() === "completed";

  async function syncTask(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await exportToGithub(task.id, repoUrl.trim());
      setIssueUrl(result.external_ticket_url);
      setShowForm(false);
    } catch (syncError) {
      setError(syncError.message || "Could not sync this action item.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md border text-xs ${completed ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300 bg-white text-transparent"}`} aria-label={completed ? "Completed" : "Not completed"}>{completed ? "✓" : "✓"}</span>
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-medium leading-6 ${completed ? "text-slate-400 line-through" : "text-slate-800"}`}>{task.description}</p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
            {task.owner && <span>Owner: {task.owner}</span>}
            {task.due_date && <span>Due: {formatDate(task.due_date).split(",")[0]}{task.due_time ? ` at ${task.due_time.slice(0, 5)}` : ""}</span>}
            {task.status && <span className="rounded-full bg-slate-100 px-2 py-0.5">{task.status}</span>}
          </div>
          {issueUrl && <a href={issueUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex text-xs font-semibold text-indigo-600 hover:text-indigo-800">View GitHub issue ↗</a>}
          {error && <p role="alert" className="mt-2 text-xs text-rose-600">{error}</p>}
          {showForm && (
            <form onSubmit={syncTask} className="mt-3 flex flex-col gap-2 sm:flex-row">
              <label className="sr-only" htmlFor={`repo-${task.id}`}>GitHub repository URL</label>
              <input id={`repo-${task.id}`} type="url" required value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/org/repository" className="h-10 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
              <button disabled={busy} className="h-10 rounded-lg bg-indigo-600 px-3 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-60">{busy ? "Syncing…" : "Create issue"}</button>
              <button type="button" onClick={() => setShowForm(false)} className="h-10 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-600 hover:bg-slate-50">Cancel</button>
            </form>
          )}
          {!showForm && !issueUrl && <button type="button" onClick={() => setShowForm(true)} className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700">↗ Sync to GitHub</button>}
          <div className="mt-3 border-t border-slate-100 pt-3">
            {task.due_date ? <>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Calendar deadline</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {calendarConnections.map((connection) => {
                  const providerName = connection.provider === "google" ? "Google Calendar" : "Outlook Calendar";
                  const key = `${connection.provider}:${task.id}`;
                  const result = calendarResults[key];
                  const syncError = calendarErrors[key];
                  if (connection.connected) {
                    return <div key={connection.provider} className="flex flex-col gap-1">
                      <button type="button" onClick={() => void onSyncToCalendar(connection.provider, task.id)} disabled={calendarLoading || calendarBusy === key} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-sky-200 hover:bg-sky-50 hover:text-sky-800 disabled:cursor-wait disabled:opacity-60">
                        {calendarBusy === key ? "Adding…" : result ? (result.already_synced ? `Already in ${connection.provider === "google" ? "Google" : "Outlook"}` : "Added to calendar") : `Add to ${connection.provider === "google" ? "Google" : "Outlook"}`}
                      </button>
                      {result?.event_url && <a href={result.event_url} target="_blank" rel="noreferrer" className="text-[11px] font-medium text-sky-700 hover:underline">View calendar event ↗</a>}
                      {syncError && <span role="alert" className="max-w-xs text-[11px] leading-4 text-rose-600">{syncError}</span>}
                    </div>;
                  }
                  if (connection.configured) {
                    return <button key={connection.provider} type="button" onClick={() => connectCalendar(connection.provider)} disabled={calendarLoading} className="min-h-9 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-sky-200 hover:bg-sky-50 hover:text-sky-800 disabled:opacity-60">Connect {connection.provider === "google" ? "Google" : "Outlook"}</button>;
                  }
                  return null;
                })}
                {!calendarLoading && calendarConnections.length > 0 && !calendarConnections.some((connection) => connection.connected || connection.configured) && <span className="self-center text-xs text-slate-500">Calendar integration needs credentials in the backend environment.</span>}
                {calendarLoading && <span className="self-center text-xs text-slate-400">Checking connected calendars…</span>}
              </div>
              {calendarConnections.length > 0 && !calendarConnections.some((connection) => connection.connected || connection.configured) && <p className="mt-1 text-[11px] text-slate-400">Configure a provider in the backend environment to enable calendar sync.</p>}
            </> : <p className="text-xs leading-5 text-slate-500">No due date was identified for this action item, so it cannot be added as a dated calendar event.</p>}
          </div>
        </div>
      </div>
    </li>
  );
}

function TranscriptPanel({ meeting, segments }) {
  const speakerIds = [...new Set(segments.map((segment) => segment.speaker_id).filter(Boolean))];
  const [speakerIdentities, setSpeakerIdentities] = useState(meeting.speaker_identities ?? {});
  const [nameEdits, setNameEdits] = useState({});
  const [savingSpeaker, setSavingSpeaker] = useState(null);
  const [inferringNames, setInferringNames] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveMessage, setSaveMessage] = useState("");

  useEffect(() => {
    const identities = meeting.speaker_identities ?? {};
    setSpeakerIdentities(identities);
    setNameEdits(Object.fromEntries(
      Object.entries(identities).map(([speakerId, identity]) => [speakerId, identity.name ?? ""]),
    ));
  }, [meeting.id, meeting.speaker_identities]);

  async function saveSpeakerName(event, speakerId) {
    event.preventDefault();
    setSavingSpeaker(speakerId);
    setSaveError("");
    setSaveMessage("");
    try {
      const result = await updateSpeakerIdentity(
        meeting.id,
        speakerId,
        nameEdits[speakerId] ?? speakerIdentities[speakerId]?.name ?? "",
      );
      const identities = result.speaker_identities ?? {};
      setSpeakerIdentities(identities);
      setNameEdits((current) => ({
        ...current,
        [speakerId]: identities[speakerId]?.name ?? "",
      }));
      setSaveMessage(`${speakerId.replaceAll("_", " ")} name saved.`);
    } catch (error) {
      setSaveError(error.message || "Could not save the speaker name.");
    } finally {
      setSavingSpeaker(null);
    }
  }

  async function inferNames() {
    setInferringNames(true);
    setSaveError("");
    setSaveMessage("");
    try {
      const result = await inferSpeakerIdentities(meeting.id);
      const identities = result.speaker_identities ?? {};
      setSpeakerIdentities(identities);
      setNameEdits(Object.fromEntries(
        Object.entries(identities).map(([speakerId, identity]) => [speakerId, identity.name ?? ""]),
      ));
      setSaveMessage(Object.keys(identities).length
        ? "Speaker names were checked. Review inferred labels below."
        : "No names could be identified confidently. You can enter them manually below.");
    } catch (error) {
      setSaveError(error.message || "Could not infer speaker names.");
    } finally {
      setInferringNames(false);
    }
  }

  return (
    <details className="mt-5 rounded-xl border border-slate-200">
      <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-slate-700">View redacted transcript</summary>
      <div className="max-h-[38rem] overflow-auto border-t border-slate-100 px-4 py-2">
        {speakerIds.length > 0 && <div className="space-y-3 border-b border-slate-100 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-800">Speaker names</p>
            <p className="mt-1 text-xs leading-5 text-slate-500">Names are inferred from introductions or clear name-and-reply turns. Review or edit them here.</p>
          </div>
          <button type="button" onClick={() => void inferNames()} disabled={inferringNames} className="h-9 self-start rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60">
            {inferringNames ? "Checking transcript…" : "Check transcript for names"}
          </button>
          {speakerIds.map((speakerId) => {
            const identity = speakerIdentities[speakerId];
            const label = identity?.name || "";
            const confidence = Number(identity?.confidence);
            const identityStatus = identity?.confirmed
              ? "Confirmed by you"
              : label && Number.isFinite(confidence)
                ? `Inferred · ${Math.round(confidence * 100)}% confidence`
                : "Name not identified";
            return <form key={speakerId} onSubmit={(event) => void saveSpeakerName(event, speakerId)} className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_auto] sm:items-end">
              <div>
                <label htmlFor={`speaker-name-${meeting.id}-${speakerId}`} className="text-xs font-semibold text-indigo-700">{speakerId.replaceAll("_", " ")}</label>
                <p className="mt-1 text-[11px] text-slate-400">{identityStatus}</p>
              </div>
              <input
                id={`speaker-name-${meeting.id}-${speakerId}`}
                type="text"
                maxLength={120}
                value={nameEdits[speakerId] ?? label}
                onChange={(event) => setNameEdits((current) => ({ ...current, [speakerId]: event.target.value }))}
                placeholder="Name not identified"
                className="h-10 min-w-0 rounded-lg border border-slate-200 px-3 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
              />
              <button type="submit" disabled={savingSpeaker !== null} className="h-10 rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white hover:bg-slate-700 disabled:cursor-wait disabled:opacity-60">
                {savingSpeaker === speakerId ? "Saving…" : label ? "Save / clear" : "Save name"}
              </button>
            </form>;
          })}
          {saveError && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{saveError}</p>}
          {saveMessage && <p role="status" className="text-xs text-emerald-700">{saveMessage}</p>}
        </div>}
        {segments.length ? <ol className="divide-y divide-slate-100">{segments.map((segment, index) => {
          const seconds = Number(segment.start_seconds ?? 0);
          const time = `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
          const identity = segment.speaker_id ? speakerIdentities[segment.speaker_id] : null;
          const speakerLabel = identity?.name || (segment.speaker_id ? segment.speaker_id.replaceAll("_", " ") : "Speaker unknown");
          return <li key={`${segment.start_seconds}-${index}`} className="grid gap-1 py-3 text-sm sm:grid-cols-[4rem_9rem_1fr]">
            <span className="font-mono text-xs tabular-nums text-slate-400">{time}</span>
            <span className="text-xs font-semibold text-indigo-700">{speakerLabel}</span>
            <span className="whitespace-pre-wrap leading-6 text-slate-600">{segment.text}</span>
          </li>;
        })}</ol> : <p className="whitespace-pre-wrap py-3 text-sm leading-6 text-slate-600">{meeting.transcript}</p>}
      </div>
    </details>
  );
}

export default function MeetingIntelligenceView({ meeting, loading = false, onBack }) {
  const [calendarConnections, setCalendarConnections] = useState([]);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarConnectionError, setCalendarConnectionError] = useState("");
  const [calendarBusy, setCalendarBusy] = useState(null);
  const [calendarResults, setCalendarResults] = useState({});
  const [calendarErrors, setCalendarErrors] = useState({});

  useEffect(() => {
    if (!meeting?.id) return undefined;
    let active = true;
    setCalendarLoading(true);
    setCalendarConnectionError("");
    getCalendarConnections()
      .then((result) => {
        if (active) setCalendarConnections(Array.isArray(result?.connections) ? result.connections : []);
      })
      .catch((error) => {
        if (active) setCalendarConnectionError(error.message || "Could not check calendar connections.");
      })
      .finally(() => {
        if (active) setCalendarLoading(false);
      });
    return () => { active = false; };
  }, [meeting?.id]);

  if (loading) {
    return <div role="status" className="rounded-2xl border border-slate-200 bg-white p-8 text-sm text-slate-500">Loading meeting intelligence…</div>;
  }
  if (!meeting) {
    return <div className="rounded-2xl border border-slate-200 bg-white p-8 text-sm text-slate-500">Select a meeting to view its intelligence.</div>;
  }

  const actionItems = meeting.action_items ?? [];
  const decisions = meeting.decisions ?? [];
  const risks = meeting.risks ?? [];
  const metrics = meeting.behavioral_metrics ?? {};
  const transcriptSegments = meeting.transcript_segments ?? [];

  async function addTaskToCalendar(provider, actionItemId) {
    const key = `${provider}:${actionItemId}`;
    setCalendarBusy(key);
    setCalendarErrors((current) => ({ ...current, [key]: "" }));
    try {
      const result = await syncTaskToCalendar(provider, actionItemId);
      setCalendarResults((current) => ({ ...current, [key]: result }));
    } catch (error) {
      setCalendarErrors((current) => ({ ...current, [key]: error.message || "Could not add this task to the calendar." }));
    } finally {
      setCalendarBusy(null);
    }
  }

  return (
    <article className="mx-auto w-full max-w-5xl space-y-5">
      <header className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-8">
        <button type="button" onClick={onBack} className="mb-5 inline-flex items-center gap-2 text-xs font-semibold text-slate-500 hover:text-indigo-700"><span aria-hidden="true">←</span> All meetings</button>
        <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-indigo-600">Meeting intelligence</p>
            <h1 className="mt-2 break-words text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{meeting.title}</h1>
            <p className="mt-2 text-sm text-slate-500">Recorded {formatDate(meeting.date_recorded)}</p>
          </div>
          <div className="w-full shrink-0 rounded-2xl bg-slate-50 p-4 sm:w-72">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Spoken recap</p>
            <audio className="mt-3 w-full" controls preload="none" src={`/api/v1/rag/briefing/${encodeURIComponent(meeting.id)}`}>
              Your browser does not support the audio player.
            </audio>
          </div>
        </div>
        {meeting.key_topics?.length > 0 && <div className="mt-6 flex flex-wrap gap-2">{meeting.key_topics.map((topic, index) => <span key={`${topic}-${index}`} className="rounded-full border border-indigo-100 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700">{topic}</span>)}</div>}
      </header>

      <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
        <SectionHeading eyebrow="At a glance" title="Executive summary" />
        <p className="mt-4 whitespace-pre-line text-sm leading-7 text-slate-600">{meeting.executive_summary || "No executive summary is available for this meeting."}</p>
      </section>

      {meeting.executive_summary && <MeetingMinutesPanel meeting={meeting} />}

      <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
        <SectionHeading eyebrow="Follow-through" title="Action items" count={actionItems.length} />
        {calendarConnectionError && <p role="status" className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">Calendar connection status is unavailable: {calendarConnectionError}</p>}
        {actionItems.length ? <ul className="mt-4 space-y-3">{actionItems.map((task) => <TaskCard key={task.id} task={task} calendarConnections={calendarConnections} calendarLoading={calendarLoading} onSyncToCalendar={addTaskToCalendar} calendarBusy={calendarBusy} calendarResults={calendarResults} calendarErrors={calendarErrors} />)}</ul> : <p className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-slate-500">No action items were extracted.</p>}
      </section>

      <div className="grid gap-5 xl:grid-cols-2">
        <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
          <SectionHeading eyebrow="What was agreed" title="Decisions" count={decisions.length} />
          {decisions.length ? <ul className="mt-4 space-y-3">{decisions.map((decision) => <li key={decision.id} className="rounded-2xl border border-slate-200 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-semibold text-sky-700">{decision.state}</span>{decision.timestamp_seconds != null && <span className="text-xs tabular-nums text-slate-400">{Math.floor(decision.timestamp_seconds / 60)}:{String(Math.floor(decision.timestamp_seconds % 60)).padStart(2, "0")}</span>}</div><p className="mt-3 text-sm font-medium leading-6 text-slate-800">{decision.statement}</p>{decision.rationale && <p className="mt-2 text-sm leading-6 text-slate-500">{decision.rationale}</p>}</li>)}</ul> : <p className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-slate-500">No decisions were extracted.</p>}
        </section>

        <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
          <SectionHeading eyebrow="Watch list" title="Project risks" count={risks.length} />
          {risks.length ? <ul className="mt-4 space-y-3">{risks.map((risk, index) => <li key={`${risk.description}-${index}`} className="rounded-2xl border border-slate-200 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><p className="flex-1 text-sm font-medium leading-6 text-slate-800">{risk.description}</p><span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${severityStyles(risk.severity)}`}>{risk.severity}</span></div>{risk.owner && <p className="mt-2 text-xs text-slate-500">Owner: {risk.owner}</p>}{risk.mitigation && <p className="mt-3 border-l-2 border-emerald-300 pl-3 text-sm leading-6 text-slate-600"><span className="font-semibold text-slate-700">Mitigation:</span> {risk.mitigation}</p>}</li>)}</ul> : <p className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-slate-500">No project risks were identified.</p>}
        </section>
      </div>

      <section className="rounded-3xl border border-slate-200 bg-white p-5 sm:p-7">
        <SectionHeading eyebrow="Conversation signals" title="Participation snapshot" />
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[["Words", metrics.word_count], ["Estimated WPM", metrics.words_per_minute], ["Filler words", metrics.filler_word_count], ["Estimated length", metrics.estimated_duration_minutes != null ? `${metrics.estimated_duration_minutes} min` : null]].map(([label, value]) => <div key={label} className="rounded-2xl bg-slate-50 p-4"><p className="text-xs font-medium text-slate-500">{label}</p><p className="mt-2 text-xl font-semibold tracking-tight text-slate-900">{value ?? "—"}</p></div>)}
        </div>
        {meeting.diarization_status !== "completed" && <p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">Speaker labels are unavailable for this recording. Configure an accepted Hugging Face token for the gated pyannote model to enable diarization.</p>}
        {meeting.transcript && <TranscriptPanel meeting={meeting} segments={transcriptSegments} />}
      </section>
    </article>
  );
}
