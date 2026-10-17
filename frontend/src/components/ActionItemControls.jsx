import { useEffect, useState } from "react";
import { updateActionItem } from "../lib/api";

export default function ActionItemControls({ task, onUpdated, compact = false }) {
  const [editing, setEditing] = useState(false);
  const [dueDate, setDueDate] = useState(task.due_date || "");
  const [dueTime, setDueTime] = useState(task.due_time?.slice(0, 5) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const completed = String(task.status || "").toLowerCase() === "completed";

  useEffect(() => {
    setDueDate(task.due_date || "");
    setDueTime(task.due_time?.slice(0, 5) || "");
  }, [task.id, task.due_date, task.due_time]);

  async function saveDeadline(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await updateActionItem(task.id, {
        due_date: dueDate,
        due_time: dueTime || null,
      });
      onUpdated?.(updated);
      setEditing(false);
      setMessage(updated.sync_warnings?.length
        ? `Deadline saved. A linked service needs attention: ${updated.sync_warnings.join("; ")}`
        : "Deadline saved.");
    } catch (saveError) {
      setError(saveError.message || "Could not save the task deadline.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleComplete() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await updateActionItem(task.id, {
        status: completed ? "Promised" : "Completed",
      });
      onUpdated?.(updated);
      setMessage(completed ? "Task reopened." : "Task marked complete.");
    } catch (saveError) {
      setError(saveError.message || "Could not update the task status.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`mt-3 ${compact ? "space-y-2" : "space-y-2"}`}>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => { setEditing((value) => !value); setError(""); }} disabled={busy} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-60">
          {editing ? "Cancel deadline edit" : task.due_date ? "Edit deadline" : "Add due date & time"}
        </button>
        <button type="button" onClick={() => void toggleComplete()} disabled={busy} className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition disabled:opacity-60 ${completed ? "border-amber-200 text-amber-800 hover:bg-amber-50" : "border-emerald-200 text-emerald-800 hover:bg-emerald-50"}`}>
          {busy ? "Saving…" : completed ? "Reopen task" : "Mark complete"}
        </button>
      </div>
      {editing && <form onSubmit={(event) => void saveDeadline(event)} className="flex flex-col gap-2 rounded-xl bg-slate-50 p-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <label htmlFor={`task-date-${task.id}`} className="mb-1 block text-[11px] font-semibold text-slate-600">Due date</label>
          <input id={`task-date-${task.id}`} type="date" required value={dueDate} onChange={(event) => setDueDate(event.target.value)} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
        </div>
        <div className="w-full sm:w-36">
          <label htmlFor={`task-time-${task.id}`} className="mb-1 block text-[11px] font-semibold text-slate-600">Time (optional)</label>
          <input id={`task-time-${task.id}`} type="time" value={dueTime} onChange={(event) => setDueTime(event.target.value)} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-xs outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
        </div>
        <button type="submit" disabled={busy || !dueDate} className="h-9 rounded-lg bg-indigo-600 px-3 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-60">{busy ? "Saving…" : "Save deadline"}</button>
      </form>}
      {error && <p role="alert" className="text-xs leading-5 text-rose-700">{error}</p>}
      {message && <p role="status" className="text-xs leading-5 text-emerald-700">{message}</p>}
    </div>
  );
}
