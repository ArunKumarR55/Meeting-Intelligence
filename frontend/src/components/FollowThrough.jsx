import { useCallback, useEffect, useState } from "react";
import {
  connectCalendar,
  disconnectCalendar,
  getActionItemDrift,
  getCalendarConnections,
  getCalendarEvents,
  getFollowthroughTasks,
  getFollowthroughRisks,
  getReminderStatus,
  setFollowthroughRiskStatus,
  syncTaskToCalendar,
} from "../lib/api";

const PROVIDERS = [
  { id: "google", name: "Google Calendar", tone: "border-blue-200 bg-blue-50 text-blue-800" },
  { id: "microsoft", name: "Outlook Calendar", tone: "border-sky-200 bg-sky-50 text-sky-800" },
];

const PANELS = [
  { id: "drift", label: "Action drift", description: "Track task continuity across meetings." },
  { id: "calendar", label: "Calendar & tasks", description: "Manage connections and dated action items." },
  { id: "risks", label: "Risks & reminders", description: "Review open risks and reminder delivery." },
];

function formatDate(value) {
  if (!value) return "Date unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: value.includes("T") ? "short" : undefined,
    }).format(parsed);
}

function StatusCard({ label, value, detail }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">{label}</p>
      <p className="mt-3 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{detail}</p>
    </div>
  );
}

function DriftBadge({ value }) {
  const styles = {
    not_mentioned: "bg-amber-50 text-amber-800",
    delayed: "bg-rose-50 text-rose-700",
    blocked: "bg-orange-50 text-orange-800",
    completed: "bg-emerald-50 text-emerald-700",
    carried_forward: "bg-indigo-50 text-indigo-700",
    changed: "bg-violet-50 text-violet-700",
  };
  const labels = {
    not_mentioned: "Not mentioned",
    carried_forward: "Carried forward",
    changed: "Description changed",
  };
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-bold ${styles[value] || "bg-slate-100 text-slate-700"}`}>
      {labels[value] || value}
    </span>
  );
}

export default function FollowThrough() {
  const [activePanel, setActivePanel] = useState("drift");
  const [drift, setDrift] = useState([]);
  const [reminders, setReminders] = useState(null);
  const [connections, setConnections] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [risks, setRisks] = useState([]);
  const [eventsByProvider, setEventsByProvider] = useState({});
  const [syncResults, setSyncResults] = useState({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    const results = await Promise.allSettled([
      getActionItemDrift(),
      getReminderStatus(),
      getCalendarConnections(),
      getFollowthroughTasks(),
      getFollowthroughRisks(),
    ]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) setError(failed.reason?.message || "Could not load follow-through data.");
    if (results[0].status === "fulfilled") setDrift(results[0].value.items || []);
    if (results[1].status === "fulfilled") setReminders(results[1].value);
    if (results[2].status === "fulfilled") setConnections(results[2].value.connections || []);
    if (results[3].status === "fulfilled") setTasks(results[3].value.items || []);
    if (results[4].status === "fulfilled") setRisks(results[4].value.items || []);
    setLoading(false);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.get("calendar");
    const status = params.get("status");
    if (provider && status) {
      const providerName = provider === "google" ? "Google" : "Outlook";
      setNotice(status === "connected"
        ? `${providerName} Calendar connected successfully.`
        : `${providerName} Calendar authorization was cancelled.`);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
    void refresh();
  }, [refresh]);

  const connected = (provider) => connections.find((item) => item.provider === provider)?.connected;
  const sortedDrift = [...drift].sort((first, second) => {
    const firstDate = new Date(first.created_at || 0).valueOf();
    const secondDate = new Date(second.created_at || 0).valueOf();
    return secondDate - firstDate
      || Number(second.observed_meeting_id || second.source_meeting_id || 0)
        - Number(first.observed_meeting_id || first.source_meeting_id || 0)
      || Number(second.id || 0) - Number(first.id || 0);
  });

  async function loadEvents(provider) {
    setBusy(`events:${provider}`);
    setError("");
    try {
      const result = await getCalendarEvents(provider);
      setEventsByProvider((previous) => ({ ...previous, [provider]: result.events || [] }));
    } catch (requestError) {
      setError(requestError.message || "Could not load calendar events.");
    } finally {
      setBusy("");
    }
  }

  async function disconnect(provider) {
    setBusy(`disconnect:${provider}`);
    setError("");
    try {
      await disconnectCalendar(provider);
      await refresh();
      setEventsByProvider((previous) => ({ ...previous, [provider]: [] }));
    } catch (requestError) {
      setError(requestError.message || "Could not disconnect the calendar.");
    } finally {
      setBusy("");
    }
  }

  async function syncTask(provider, taskId) {
    const key = `${provider}:${taskId}`;
    setBusy(`sync:${key}`);
    setError("");
    try {
      const result = await syncTaskToCalendar(provider, taskId);
      setSyncResults((previous) => ({ ...previous, [key]: result }));
    } catch (requestError) {
      setError(requestError.message || "Could not add the task deadline to the calendar.");
    } finally {
      setBusy("");
    }
  }

  async function updateRisk(risk, nextStatus) {
    const key = `${risk.meeting_id}:${risk.risk_index}`;
    setBusy(`risk:${key}`);
    setError("");
    try {
      await setFollowthroughRiskStatus(risk.meeting_id, risk.risk_index, nextStatus);
      setRisks((previous) => previous.map((item) => (
        `${item.meeting_id}:${item.risk_index}` === key ? { ...item, status: nextStatus } : item
      )));
      setReminders(await getReminderStatus());
    } catch (requestError) {
      setError(requestError.message || "Could not update risk status.");
    } finally {
      setBusy("");
    }
  }

  const cards = [
    { label: "Overdue tasks", value: reminders?.overdue_task_count ?? "—", detail: "Open action items past their deadline" },
    { label: "Open risks", value: reminders?.open_risk_count ?? "—", detail: "Risks currently tracked in meeting records" },
    { label: "Drift observations", value: drift.length, detail: "Follow-up signals across meetings" },
  ];

  return (
    <div className="space-y-6">
      {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"><span>{error}</span><button type="button" onClick={() => void refresh()} className="font-semibold underline">Retry</button></div>}
      {notice && <div role="status" className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">{notice}</div>}

      <section className="grid gap-4 sm:grid-cols-3">
        {cards.map((card) => <StatusCard key={card.label} {...card} />)}
      </section>

      <nav aria-label="Follow-through sections" className="grid gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm sm:grid-cols-3">
        {PANELS.map((panel) => {
          const selected = activePanel === panel.id;
          const count = panel.id === "drift" ? drift.length : panel.id === "calendar" ? tasks.length : risks.length;
          return <button key={panel.id} type="button" aria-current={selected ? "page" : undefined} onClick={() => setActivePanel(panel.id)} className={`rounded-xl px-4 py-3 text-left transition ${selected ? "bg-indigo-600 text-white shadow-sm" : "text-slate-700 hover:bg-slate-50"}`}>
            <span className="flex items-center justify-between gap-3"><span className="text-sm font-semibold">{panel.label}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${selected ? "bg-white/15 text-white" : "bg-slate-100 text-slate-500"}`}>{count}</span></span>
            <span className={`mt-1 block text-[11px] ${selected ? "text-indigo-100" : "text-slate-500"}`}>{panel.description}</span>
          </button>;
        })}
      </nav>

      {activePanel === "drift" && <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-indigo-600">Task continuity · newest first</p><h2 className="mt-1 text-base font-bold text-slate-900">Cross-meeting action drift</h2></div><button type="button" onClick={() => void refresh()} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50">Refresh</button></div>
        <p className="mt-2 text-xs leading-5 text-slate-500">A missing mention is a prompt for review, not proof that a task was dropped. AMIP never changes a task’s status automatically.</p>
        <div className="mt-4 space-y-3">
          {loading ? <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500">Loading observations…</p> : sortedDrift.length === 0 ? <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500">No cross-meeting comparisons yet. New uploads will be checked against earlier open tasks.</p> : sortedDrift.slice(0, 50).map((item) => <article key={item.id} className="rounded-2xl border border-slate-100 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><DriftBadge value={item.drift_type} /><span className="text-[10px] font-semibold text-slate-400">{item.match_score ? `${Math.round(item.match_score * 100)}% match` : "No matching task"}</span></div><h3 className="mt-3 text-sm font-semibold leading-5 text-slate-900">{item.prior_description}</h3><p className="mt-1 text-[11px] text-slate-500">{item.source_meeting_title}{item.prior_owner ? ` · ${item.prior_owner}` : ""}{item.prior_due_date ? ` · Due ${item.prior_due_date}` : ""}</p><p className="mt-3 text-xs leading-5 text-slate-600">{item.evidence}</p>{item.observed_description && <div className="mt-3 rounded-xl bg-slate-50 p-3"><p className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Later meeting · {item.observed_meeting_title}</p><p className="mt-1 text-xs leading-5 text-slate-700">{item.observed_description}</p></div>}</article>)}
        </div>
      </section>}

      {activePanel === "calendar" && <div className="space-y-5">
        <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-sky-700">Calendar integrations</p><h2 className="mt-1 text-base font-bold text-slate-900">Connected calendars</h2><p className="mt-2 text-xs leading-5 text-slate-500">Add dated AMIP tasks as all-day or timed events, depending on the deadline captured from the meeting.</p></div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">{PROVIDERS.map((provider) => {
            const status = connections.find((item) => item.provider === provider.id);
            return <div key={provider.id} className="rounded-2xl border border-slate-100 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold ${provider.tone}`}>{provider.name}</span><p className="mt-2 text-xs text-slate-600">{status?.connected ? status.account_email || "Connected account" : "Not connected"}</p></div>{status?.connected ? <div className="flex gap-2"><button type="button" onClick={() => void loadEvents(provider.id)} disabled={busy === `events:${provider.id}`} className="rounded-lg bg-slate-900 px-3 py-2 text-[10px] font-semibold text-white disabled:opacity-50">{busy === `events:${provider.id}` ? "Loading…" : "Upcoming"}</button><button type="button" onClick={() => void disconnect(provider.id)} disabled={busy === `disconnect:${provider.id}`} className="rounded-lg border border-slate-200 px-3 py-2 text-[10px] font-semibold text-slate-600 disabled:opacity-50">Disconnect</button></div> : status?.configured ? <button type="button" onClick={() => connectCalendar(provider.id)} className="rounded-lg bg-indigo-600 px-3 py-2 text-[10px] font-semibold text-white hover:bg-indigo-700">Connect</button> : <button type="button" disabled className="cursor-not-allowed rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] font-semibold text-slate-400">Needs setup</button>}</div>
              {!status?.connected && !status?.configured && <p className="mt-3 text-[10px] leading-4 text-slate-500">Add the provider client ID, client secret, and token encryption key to backend/.env to enable this connection.</p>}
              {eventsByProvider[provider.id] && <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">{eventsByProvider[provider.id].length ? eventsByProvider[provider.id].slice(0, 5).map((event) => <div key={event.id} className="rounded-xl bg-slate-50 px-3 py-2"><p className="truncate text-xs font-semibold text-slate-800">{event.subject}</p><p className="mt-1 text-[10px] text-slate-500">{formatDate(event.start)}{event.location ? ` · ${event.location}` : ""}</p></div>) : <p className="text-xs text-slate-500">No upcoming events in the next 30 days.</p>}</div>}
            </div>;
          })}</div>
        </section>

        <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-indigo-600">Calendar-ready work</p><h2 className="mt-1 text-base font-bold text-slate-900">Action-item deadlines</h2><p className="mt-1 text-xs text-slate-500">Tasks with a detected due date appear here. Sync is idempotent per provider.</p></div>
          <div className="mt-4 divide-y divide-slate-100">{loading ? <p className="py-6 text-center text-sm text-slate-500">Loading dated tasks…</p> : tasks.length === 0 ? <p className="py-6 text-center text-sm text-slate-500">No open action items have a due date.</p> : tasks.map((task) => <div key={task.id} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-semibold leading-5 text-slate-800">{task.description}</p><p className="mt-1 text-[11px] text-slate-500">{task.meeting_title}{task.owner ? ` · ${task.owner}` : " · Unassigned"} · Due {task.due_date}{task.due_time ? ` at ${task.due_time}` : ""}</p></div><div className="flex flex-wrap gap-2">{PROVIDERS.filter((provider) => connected(provider.id)).map((provider) => { const key = `${provider.id}:${task.id}`; const result = syncResults[key]; return <div key={provider.id} className="flex flex-col gap-1"><button type="button" onClick={() => void syncTask(provider.id, task.id)} disabled={busy === `sync:${key}`} className="rounded-lg border border-slate-200 px-3 py-2 text-[10px] font-semibold text-slate-700 hover:border-indigo-200 hover:bg-indigo-50 disabled:opacity-50">{busy === `sync:${key}` ? "Adding…" : result ? (result.already_synced ? "Already added" : "Added") : `Add to ${provider.id === "google" ? "Google" : "Outlook"}`}</button>{result?.event_url && <a href={result.event_url} target="_blank" rel="noreferrer" className="text-[10px] font-semibold text-indigo-600 hover:underline">View event ↗</a>}</div>; })}{!PROVIDERS.some((provider) => connected(provider.id)) && <span className="self-center text-[10px] text-slate-400">Connect a calendar above to sync</span>}</div></div>)}</div>
        </section>
      </div>}

      {activePanel === "risks" && <div className="space-y-5">
        <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-700">Automated notifications</p><h2 className="mt-1 text-base font-bold text-slate-900">Reminder delivery</h2>
          <div className="mt-4 rounded-2xl bg-slate-50 p-4"><div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-slate-700">Scheduler</span><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${reminders?.enabled ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-600"}`}>{reminders?.enabled ? "Enabled" : "Disabled"}</span></div><p className="mt-2 text-xs leading-5 text-slate-500">Checks every {reminders?.interval_minutes ?? "—"} minutes; at most one digest per recipient per day. Daily delivery timezone: {reminders?.timezone || "—"}.</p><p className="mt-2 text-[10px] text-slate-400">Last successful delivery: {reminders?.last_delivery_at ? formatDate(reminders.last_delivery_at) : "None yet"}</p></div>
          <p className="mt-3 text-[11px] leading-5 text-slate-500">Reminders go to the recipients saved for each meeting. If a meeting has no saved recipients, AMIP uses the configured SMTP sender address.</p>
        </section>

        <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-rose-700">Risk register</p><h2 className="mt-1 text-base font-bold text-slate-900">Open project risks</h2><p className="mt-1 text-xs text-slate-500">Newly extracted risks start open. Resolve an item to stop including it in reminder emails.</p></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">{loading ? <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500 md:col-span-2">Loading risks…</p> : risks.length === 0 ? <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500 md:col-span-2">No risks have been extracted from meetings.</p> : risks.map((risk) => { const key = `${risk.meeting_id}:${risk.risk_index}`; return <article key={key} className={`rounded-2xl border p-4 ${risk.status === "resolved" ? "border-slate-100 bg-slate-50/70" : "border-rose-100 bg-white"}`}><div className="flex items-start justify-between gap-3"><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${risk.severity === "Critical" || risk.severity === "High" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-800"}`}>{risk.severity}</span><span className={`text-[10px] font-bold uppercase tracking-wide ${risk.status === "resolved" ? "text-emerald-700" : "text-rose-600"}`}>{risk.status}</span></div><h3 className="mt-3 text-sm font-semibold leading-5 text-slate-900">{risk.description}</h3><p className="mt-1 text-[11px] text-slate-500">{risk.meeting_title}{risk.owner ? ` · ${risk.owner}` : ""}</p>{risk.mitigation && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-600"><span className="font-semibold">Mitigation:</span> {risk.mitigation}</p>}<button type="button" onClick={() => void updateRisk(risk, risk.status === "resolved" ? "open" : "resolved")} disabled={busy === `risk:${key}`} className="mt-3 rounded-lg border border-slate-200 px-3 py-2 text-[10px] font-semibold text-slate-600 hover:bg-white disabled:opacity-50">{busy === `risk:${key}` ? "Saving…" : risk.status === "resolved" ? "Reopen risk" : "Mark resolved"}</button></article>; })}</div>
        </section>
      </div>}
    </div>
  );
}
