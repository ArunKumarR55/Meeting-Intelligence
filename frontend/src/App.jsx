import { useCallback, useEffect, useMemo, useState } from "react";
import AudioUploader from "./components/AudioUploader";
import FollowThrough from "./components/FollowThrough";
import MeetingIntelligenceView from "./components/MeetingIntelligenceView";
import { getMeetingDetails, getMeetings, queryRAG } from "./lib/api";

const NAVIGATION = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "meetings", label: "Meetings", icon: "meetings" },
  { id: "followthrough", label: "Follow-through", icon: "followthrough" },
  { id: "memory", label: "Meeting memory", icon: "memory" },
];

const PAGE_COPY = {
  overview: ["Workspace overview", "A clear view of what your conversations are moving forward."],
  meetings: ["Meetings", "Review summaries, decisions, risks, and follow-through."],
  followthrough: ["Follow-through", "Track task continuity, reminders, and calendar deadlines."],
  memory: ["Meeting memory", "Find the details and decisions held across your meetings."],
  upload: ["New meeting", "Turn a recording into structured, searchable meeting intelligence."],
};

function formatMeetingDate(value, options = { dateStyle: "medium" }) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Date unavailable"
    : new Intl.DateTimeFormat(undefined, options).format(date);
}

function Icon({ name, className = "h-5 w-5" }) {
  const paths = {
    overview: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="4" rx="1.5" /><rect x="13.5" y="9.5" width="7" height="11" rx="1.5" /><rect x="3.5" y="12.5" width="7" height="8" rx="1.5" /></>,
    meetings: <><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M7.5 3.5v3M16.5 3.5v3M3.5 9.5h17M7.5 13h3M7.5 16.5h7" /></>,
    memory: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h12A2.5 2.5 0 0 1 21 5.5v11a2.5 2.5 0 0 1-2.5 2.5h-12A2.5 2.5 0 0 1 4 16.5z" /><path d="M8 8h9M8 12h9M8 16h5" /><path d="M2 7v11" /></>,
    followthrough: <><path d="M5 12.5 9 16l10-9" /><path d="M21 12a9 9 0 1 1-5.4-8.25" /><path d="M12 7.5v5l3 1.8" /></>,
    upload: <><path d="M12 15V4m0 0L8 8m4-4 4 4" /><path d="M4.5 14.5v3A2.5 2.5 0 0 0 7 20h10a2.5 2.5 0 0 0 2.5-2.5v-3" /></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.3" /><path d="m16 16 4.2 4.2" /></>,
    lock: <><rect x="4.5" y="10" width="15" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v2" /></>,
    calendar: <><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M7.5 3.5v3M16.5 3.5v3M3.5 9.5h17M8 13h.01M12 13h.01M16 13h.01M8 16.5h.01M12 16.5h.01" /></>,
    check: <><path d="m5 12.5 4.2 4.2L19.5 6.5" /></>,
    sparkle: <><path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-2-5.8L4 11l6-2.2L12 3Z" /><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">{paths[name] ?? paths.overview}</svg>;
}

function Brand() {
  return (
    <div className="flex items-center gap-3 px-1">
      <div className="grid h-10 w-10 place-items-center rounded-[14px] bg-white text-indigo-700 shadow-lg shadow-black/10">
        <span className="text-lg font-black tracking-[-0.08em]">A</span>
      </div>
      <div>
        <p className="text-[15px] font-bold tracking-[-0.03em] text-white">AMIP</p>
        <p className="mt-0.5 text-[10px] font-medium tracking-[0.12em] text-indigo-200/70">MEETING INTELLIGENCE</p>
      </div>
    </div>
  );
}

function MeetingRow({ meeting, onSelect, compact = false }) {
  const title = meeting.title || "Untitled meeting";
  return (
    <button type="button" onClick={() => onSelect(meeting.id)} className="meeting-row group flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left transition hover:bg-slate-50">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600"><Icon name="meetings" className="h-[18px] w-[18px]" /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-slate-800 group-hover:text-indigo-700">{title}</span>
        {!compact && meeting.summary_preview && <span className="mt-1 block truncate text-xs text-slate-500">{meeting.summary_preview}</span>}
        <span className="mt-1 block text-[11px] text-slate-400">{formatMeetingDate(meeting.date_recorded, { dateStyle: "medium", timeStyle: "short" })}</span>
      </span>
      <Icon name="arrow" className="h-4 w-4 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500" />
    </button>
  );
}

function ChatPanel({ onSelectMeeting }) {
  const [question, setQuestion] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function search(event) {
    event.preventDefault();
    if (!question.trim()) return;
    setError("");
    setLoading(true);
    try {
      setResults(await queryRAG(question));
    } catch (searchError) {
      setError(searchError.message || "Meeting memory search failed.");
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  const suggestions = [
    "What decisions did we make recently?",
    "Which action items are still open?",
    "What risks were raised across meetings?",
  ];

  return (
    <section className="overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-sm">
      <div className="flex items-start gap-4 border-b border-slate-100 px-5 py-5 sm:px-7">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-violet-50 text-violet-700"><Icon name="memory" /></span>
        <div><p className="text-sm font-bold text-slate-900">Ask your meeting memory</p><p className="mt-1 text-xs leading-5 text-slate-500">Search across redacted transcripts and open cited meetings.</p></div>
      </div>
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.85fr)]">
        <form onSubmit={search} className="border-b border-slate-100 p-5 sm:p-7 lg:border-b-0 lg:border-r">
          <label htmlFor="memory-query" className="mb-2 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Your question</label>
          <textarea id="memory-query" rows={5} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about decisions, owners, deadlines, or project risks…" maxLength={10_000} className="w-full resize-y rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3.5 text-sm leading-6 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100" />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-slate-400"><Icon name="lock" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Queries use your local meeting memory.</p>
            <button disabled={loading || !question.trim()} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300">
              {loading ? <><span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />Searching…</> : <>Search memory <Icon name="arrow" className="h-4 w-4" /></>}
            </button>
          </div>
          {error && <p role="alert" className="mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">{error}</p>}
          {results && <div className="mt-5 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-4" aria-live="polite">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-indigo-700">Grounded answer</p>
            {results.result_count === 0 ? <p className="mt-2 text-sm leading-6 text-slate-600">No matching transcript excerpts were found. Try a different name or topic.</p> : <p className="mt-2 whitespace-pre-line text-sm leading-6 text-slate-800">{results.answer || "Relevant meeting excerpts were found."}</p>}
            {results.citations?.length > 0 && <div className="mt-4 border-t border-indigo-100 pt-3"><p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Sources</p><div className="mt-2 flex flex-wrap gap-2">{results.citations.map((citation) => <button key={citation.source_id} type="button" onClick={() => onSelectMeeting(citation.meeting_id)} className="rounded-lg border border-indigo-100 bg-white px-2.5 py-1.5 text-left text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100">{citation.label}</button>)}</div></div>}
          </div>}
        </form>
        <div className="p-5 sm:p-7">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Try asking</p>
          <div className="mt-3 space-y-2">{suggestions.map((suggestion) => <button key={suggestion} type="button" onClick={() => setQuestion(suggestion)} className="flex w-full items-center justify-between gap-3 rounded-xl border border-slate-100 px-3.5 py-3 text-left text-xs font-medium leading-5 text-slate-600 transition hover:border-indigo-100 hover:bg-indigo-50/60 hover:text-indigo-800"><span>{suggestion}</span><Icon name="arrow" className="h-4 w-4 shrink-0 text-slate-300" /></button>)}</div>
          <div className="mt-5 rounded-2xl bg-slate-50 p-4"><p className="text-xs font-semibold text-slate-700">Answers with evidence</p><p className="mt-1.5 text-xs leading-5 text-slate-500">Every answer is grounded in relevant transcript excerpts. Use a source citation to open the meeting and review its context.</p></div>
        </div>
      </div>
    </section>
  );
}

function StatCard({ label, value, detail, icon, tone = "indigo" }) {
  const colors = {
    indigo: "bg-indigo-50 text-indigo-600",
    emerald: "bg-emerald-50 text-emerald-600",
    amber: "bg-amber-50 text-amber-600",
  };
  return (
    <div className="amip-stat-card rounded-[22px] border border-slate-200/80 bg-white p-5 shadow-[0_10px_30px_-25px_rgba(15,23,42,0.45)] sm:p-6">
      <div className="flex items-start justify-between gap-3"><p className="text-xs font-semibold uppercase tracking-[0.1em] text-slate-500">{label}</p><span className={`grid h-10 w-10 place-items-center rounded-xl ${colors[tone]}`}><Icon name={icon} className="h-[18px] w-[18px]" /></span></div>
      <p className="mt-4 truncate text-2xl font-bold tracking-[-0.04em] text-slate-900">{value}</p>
      <p className="mt-1 truncate text-xs text-slate-500">{detail}</p>
    </div>
  );
}

function EmptyState({ onUpload }) {
  return (
    <div className="rounded-[24px] border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-indigo-50 text-indigo-600"><Icon name="meetings" className="h-6 w-6" /></span>
      <h3 className="mt-5 text-base font-bold text-slate-900">Your meeting workspace starts here</h3>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-slate-500">Upload a meeting recording to create a private transcript, decisions, follow-ups, and searchable memory.</p>
      <button type="button" onClick={onUpload} className="mt-5 inline-flex h-10 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700"><Icon name="upload" className="h-4 w-4" />Add your first recording</button>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("overview");
  const [meetings, setMeetings] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [meetingSearch, setMeetingSearch] = useState("");
  const [uploadNotice, setUploadNotice] = useState(null);

  const refreshMeetings = useCallback(async () => {
    setListError("");
    try {
      const records = await getMeetings();
      setMeetings(Array.isArray(records) ? records : []);
      return Array.isArray(records) ? records : [];
    } catch (error) {
      setListError(error.message || "Could not load your meetings.");
      return [];
    } finally {
      setLoadingList(false);
    }
  }, []);

  const openMeeting = useCallback(async (meetingId) => {
    setPage("meetings");
    setSelectedId(meetingId);
    setSelectedMeeting(null);
    setLoadingDetails(true);
    setDetailError("");
    try {
      setSelectedMeeting(await getMeetingDetails(meetingId));
    } catch (error) {
      setDetailError(error.message || "Could not load meeting details.");
    } finally {
      setLoadingDetails(false);
    }
  }, []);

  useEffect(() => { void refreshMeetings(); }, [refreshMeetings]);

  const filteredMeetings = useMemo(() => {
    const searchText = meetingSearch.trim().toLowerCase();
    return searchText ? meetings.filter((meeting) => meeting.title?.toLowerCase().includes(searchText)) : meetings;
  }, [meetings, meetingSearch]);

  async function handleUploaded(result) {
    const delivery = result?.mom_delivery;
    setUploadNotice(delivery && delivery.status !== "not_requested" ? delivery : null);
    const records = await refreshMeetings();
    const createdId = result?.meeting_id;
    if (createdId != null) {
      if (!records.some((meeting) => meeting.id === createdId)) await refreshMeetings();
      await openMeeting(createdId);
    } else {
      setPage("meetings");
    }
  }

  function navigate(destination) {
    setPage(destination);
    setDetailError("");
    if (destination !== "meetings") {
      setSelectedId(null);
      setSelectedMeeting(null);
    }
    if (destination === "upload") setUploadNotice(null);
  }

  const [pageTitle, pageDescription] = PAGE_COPY[page] ?? PAGE_COPY.overview;
  const latestMeeting = meetings[0];

  return (
    <div className="min-h-screen bg-[#f4f6fb] text-slate-900">
      <aside className="amip-sidebar z-30 border-b border-slate-200 text-white lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-[256px] lg:flex-col lg:border-b-0 lg:border-r lg:border-white/5">
        <div className="flex items-center justify-between px-4 py-4 lg:block lg:px-5 lg:pb-7 lg:pt-6"><Brand /><button type="button" onClick={() => navigate("upload")} className="inline-flex h-10 items-center gap-2 rounded-xl bg-indigo-500 px-3.5 text-xs font-semibold text-white shadow-lg shadow-indigo-950/20 transition hover:bg-indigo-400 lg:mt-7 lg:h-11 lg:w-full lg:justify-center"><Icon name="upload" className="h-4 w-4" /><span>New meeting</span></button></div>
        <nav aria-label="Main navigation" className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:overflow-visible lg:px-3">
          <p className="hidden px-3 pb-2 text-[10px] font-bold uppercase tracking-[0.16em] text-indigo-200/50 lg:block">Workspace</p>
          {NAVIGATION.map((item) => {
            const active = page === item.id || (page === "upload" && item.id === "overview");
            return <button key={item.id} type="button" onClick={() => navigate(item.id)} aria-current={page === item.id ? "page" : undefined} className={`flex shrink-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left text-xs font-semibold transition lg:w-full ${active ? "bg-white/10 text-white shadow-inner shadow-white/5" : "text-indigo-100/65 hover:bg-white/5 hover:text-white"}`}><Icon name={item.icon} className="h-[18px] w-[18px] shrink-0" /><span>{item.label}</span>{item.id === "meetings" && <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] ${active ? "bg-white/10 text-white" : "bg-white/5 text-indigo-100/55"}`}>{meetings.length}</span>}</button>;
          })}
        </nav>
        <div className="hidden px-4 pb-5 lg:block">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-3.5"><div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.65)]" /><p className="text-xs font-semibold text-white/90">Private workspace</p></div><p className="mt-2 text-[11px] leading-5 text-indigo-100/50">Audio stays local for transcription and redaction before AI analysis.</p></div>
          <p className="mt-4 px-1 text-[10px] text-indigo-100/35">AMIP · Local-first meeting intelligence</p>
        </div>
      </aside>

      <div className="lg:ml-[256px]">
        <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-[#f8f9fc]/90 backdrop-blur-xl">
          <div className="mx-auto flex min-h-[76px] max-w-[1600px] items-center justify-between gap-4 px-4 sm:px-7 xl:px-10">
            <div className="min-w-0"><p className="text-[10px] font-bold uppercase tracking-[0.15em] text-indigo-600">AMIP workspace</p><h1 className="mt-1 truncate text-lg font-bold tracking-[-0.03em] text-slate-950 sm:text-xl">{pageTitle}</h1></div>
            <div className="flex items-center gap-2 sm:gap-3"><span className="hidden items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] font-semibold text-emerald-800 sm:inline-flex"><Icon name="lock" className="h-3.5 w-3.5" />Local-first processing</span><button type="button" onClick={() => navigate("upload")} className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-600 text-white shadow-sm transition hover:bg-indigo-700 sm:hidden" aria-label="New meeting"><Icon name="upload" className="h-[18px] w-[18px]" /></button></div>
          </div>
        </header>

        <main className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 sm:px-7 sm:py-8 xl:px-10">
          <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-sm text-slate-500">{pageDescription}</p></div>{page === "meetings" && selectedId == null && meetings.length > 0 && <button type="button" onClick={() => navigate("upload")} className="hidden h-10 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 sm:inline-flex"><Icon name="upload" className="h-4 w-4" />New meeting</button>}</div>

          {listError && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"><span>{listError}</span><button type="button" onClick={() => void refreshMeetings()} className="font-semibold underline">Retry</button></div>}
          {detailError && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"><span>{detailError}</span><button type="button" onClick={() => selectedId != null && void openMeeting(selectedId)} className="font-semibold underline">Retry</button></div>}
          {uploadNotice && <div role="status" className={`rounded-2xl border px-4 py-3 text-sm ${uploadNotice.status === "sent" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : uploadNotice.status === "partial" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-rose-200 bg-rose-50 text-rose-800"}`}><p className="font-semibold">{uploadNotice.status === "sent" ? "Meeting minutes emailed" : uploadNotice.status === "partial" ? "Meeting processed; some emails could not be sent" : uploadNotice.status === "skipped_all_unsubscribed" ? "Meeting processed; selected recipients are unsubscribed" : "Meeting processed, but the email was not sent"}</p><p className="mt-1 text-xs leading-5">Sent: {uploadNotice.sent?.length ?? 0} · Unsubscribed: {uploadNotice.unsubscribed?.length ?? 0} · Failed: {uploadNotice.failed?.length ?? 0}{uploadNotice.message ? ` · ${uploadNotice.message}` : ""}</p></div>}

          {page === "overview" && <div className="space-y-6">
            <section className="amip-hero relative overflow-hidden rounded-[28px] px-6 py-7 text-white shadow-[0_24px_70px_-40px_rgba(15,23,42,0.7)] sm:px-9 sm:py-9">
              <div className="pointer-events-none absolute -right-12 -top-24 h-72 w-72 rounded-full border border-white/10" /><div className="pointer-events-none absolute -right-1 -top-12 h-52 w-52 rounded-full border border-white/[0.07]" /><div className="pointer-events-none absolute right-14 top-9 h-28 w-28 rounded-full bg-indigo-400/10 blur-2xl" />
              <div className="relative grid items-center gap-8 lg:grid-cols-[minmax(0,1fr)_auto]"><div className="max-w-2xl"><div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-indigo-100"><Icon name="sparkle" className="h-3.5 w-3.5 text-indigo-300" />Turn meetings into momentum</div><h2 className="mt-5 max-w-xl text-3xl font-semibold leading-tight tracking-[-0.045em] sm:text-[38px]">Every conversation, <span className="text-indigo-300">one step closer</span> to action.</h2><p className="mt-3 max-w-xl text-sm leading-6 text-indigo-100/70">Private transcription, clear decisions, accountable follow-through, and a memory that spans your meetings.</p><button type="button" onClick={() => navigate("upload")} className="mt-6 inline-flex h-11 items-center gap-2 rounded-xl bg-white px-4 text-xs font-bold text-indigo-900 shadow-lg shadow-black/10 transition hover:bg-indigo-50"><Icon name="upload" className="h-4 w-4" />Analyze a recording<Icon name="arrow" className="ml-1 h-4 w-4" /></button></div><div className="hidden min-w-[190px] rounded-2xl border border-white/10 bg-white/[0.05] p-4 lg:block"><p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-indigo-100/55">Your workspace</p><p className="mt-2 text-4xl font-bold tracking-[-0.06em]">{meetings.length.toString().padStart(2, "0")}</p><p className="mt-1 text-xs text-indigo-100/65">{meetings.length === 1 ? "meeting analyzed" : "meetings analyzed"}</p><div className="mt-4 flex items-center gap-2 border-t border-white/10 pt-3 text-[11px] text-emerald-200"><span className="h-1.5 w-1.5 rounded-full bg-emerald-300" />Workspace ready</div></div></div>
            </section>

            <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <StatCard label="Meeting library" value={meetings.length} detail="Recorded conversations processed" icon="meetings" />
              <StatCard label="Latest activity" value={latestMeeting ? formatMeetingDate(latestMeeting.date_recorded, { month: "short", day: "numeric" }) : "No activity"} detail={latestMeeting?.title || "Your first analysis will appear here"} icon="calendar" tone="amber" />
              <StatCard label="Data protection" value="On-device" detail="Transcription and redaction run locally" icon="lock" tone="emerald" />
            </section>

            <section className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(300px,0.7fr)]">
              <div className="rounded-[24px] border border-slate-200/80 bg-white p-5 shadow-sm sm:p-6"><div className="flex items-end justify-between gap-3"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-indigo-600">Your library</p><h2 className="mt-1 text-base font-bold tracking-[-0.02em] text-slate-900">Recent meetings</h2></div><button type="button" onClick={() => navigate("meetings")} className="text-xs font-semibold text-indigo-600 hover:text-indigo-800">View all</button></div>
                {loadingList ? <div className="mt-4 space-y-2">{[0, 1, 2].map((item) => <div key={item} className="h-[68px] animate-pulse rounded-xl bg-slate-100" />)}</div> : meetings.length ? <div className="mt-3 divide-y divide-slate-100">{meetings.slice(0, 5).map((meeting) => <MeetingRow key={meeting.id} meeting={meeting} onSelect={openMeeting} />)}</div> : <div className="mt-4"><EmptyState onUpload={() => navigate("upload")} /></div>}
              </div>
              <div className="flex flex-col rounded-[24px] border border-slate-200/80 bg-white p-5 shadow-sm sm:p-6"><div className="flex items-start justify-between gap-3"><div><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-violet-600">From conversation to action</p><h2 className="mt-1 text-base font-bold tracking-[-0.02em] text-slate-900">One thoughtful workflow</h2></div><span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-600"><Icon name="sparkle" /></span></div><div className="mt-5 space-y-4">{[["01", "Transcribe privately", "Local speech recognition and sensitive data redaction."], ["02", "Find the important parts", "Summaries, decisions, tasks, and risks in one view."], ["03", "Share what matters", "Choose MOM recipients before processing the recording."]].map(([number, title, description]) => <div key={number} className="flex gap-3"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-[10px] font-bold text-slate-500">{number}</span><div><p className="text-xs font-semibold text-slate-800">{title}</p><p className="mt-1 text-[11px] leading-5 text-slate-500">{description}</p></div></div>)}</div><button type="button" onClick={() => navigate("memory")} className="mt-auto inline-flex items-center gap-2 pt-6 text-xs font-semibold text-indigo-600 hover:text-indigo-800">Explore meeting memory<Icon name="arrow" className="h-4 w-4" /></button></div>
            </section>
          </div>}

          {page === "meetings" && (selectedId != null ? <div className="space-y-4"><button type="button" onClick={() => { setSelectedId(null); setSelectedMeeting(null); setDetailError(""); }} className="inline-flex items-center gap-2 rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:text-indigo-700"><span aria-hidden="true">←</span>Back to meeting list</button><MeetingIntelligenceView meeting={selectedMeeting} loading={loadingDetails} onBack={() => { setSelectedId(null); setSelectedMeeting(null); }} /></div> : <section className="rounded-[24px] border border-slate-200/80 bg-white p-5 shadow-sm sm:p-6"><div className="flex flex-col justify-between gap-4 md:flex-row md:items-end"><div className="shrink-0"><p className="text-[10px] font-bold uppercase tracking-[0.14em] text-indigo-600">Archive</p><h2 className="mt-1 whitespace-nowrap text-base font-bold tracking-[-0.02em] text-slate-900">All meetings <span className="ml-1 text-xs font-semibold text-slate-400">{meetings.length}</span></h2></div><div className="relative w-full flex-1 md:max-w-sm"><Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" /><label htmlFor="meeting-search" className="sr-only">Search meetings</label><input id="meeting-search" value={meetingSearch} onChange={(event) => setMeetingSearch(event.target.value)} placeholder="Search by meeting title" className="h-10 w-full rounded-xl border border-slate-200 bg-slate-50 pl-9 pr-3 text-xs outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100" /></div></div>
              {loadingList ? <div className="mt-5 space-y-2">{[0, 1, 2, 3].map((item) => <div key={item} className="h-[68px] animate-pulse rounded-xl bg-slate-100" />)}</div> : filteredMeetings.length ? <div className="mt-4 divide-y divide-slate-100">{filteredMeetings.map((meeting) => <MeetingRow key={meeting.id} meeting={meeting} onSelect={openMeeting} />)}</div> : <div className="mt-5">{meetings.length ? <p className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500">No meetings match your search.</p> : <EmptyState onUpload={() => navigate("upload")} />}</div>}
            </section>)}

          {page === "memory" && <ChatPanel onSelectMeeting={openMeeting} />}
          {page === "followthrough" && <FollowThrough />}
          {page === "upload" && <AudioUploader onUploaded={handleUploaded} />}
        </main>
        <footer className="mx-auto max-w-[1600px] px-4 pb-6 text-center text-[10px] text-slate-400 sm:px-7 xl:px-10 lg:text-right">AMIP · Local-first meeting intelligence</footer>
      </div>
    </div>
  );
}
