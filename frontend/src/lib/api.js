const API_BASE = "/api/v1";

async function request(path, options = {}, base = API_BASE) {
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      credentials: "same-origin",
      ...options,
    });
  } catch (error) {
    throw new Error(
      "Could not reach the AMIP API. Make sure the backend is running on port 8000.",
      { cause: error },
    );
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => "");

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null
        ? payload.detail ?? payload.message
        : payload;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg ?? JSON.stringify(item)).join("; ")
      : detail;
    throw new Error(
      typeof message === "string" && message.trim()
        ? message
        : `Request failed with status ${response.status}.`,
    );
  }

  return payload;
}

export function uploadMeeting(formData) {
  if (!(formData instanceof FormData)) {
    throw new TypeError("uploadMeeting expects a FormData instance.");
  }
  return request("/audio/upload", {
    method: "POST",
    body: formData,
  });
}

export function getAssemblyAIToken() {
  return request("/token", {}, "/api/assemblyai");
}

export function processLiveMeeting({ title, recipients, transcript, transcriptSegments }) {
  const cleanTranscript = String(transcript ?? "").trim();
  if (!cleanTranscript) {
    throw new TypeError("The live transcript is empty. Keep recording until some speech is transcribed.");
  }
  return request("/process_meeting", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: String(title ?? "").trim(),
      recipients,
      transcript: cleanTranscript,
      transcript_segments: Array.isArray(transcriptSegments) ? transcriptSegments : [],
    }),
  }, "/api/assemblyai");
}

export function getMomRecipients() {
  return request("/workflows/recipients");
}

export function addMomRecipient(email, displayName = "") {
  return request("/workflows/recipients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: String(email ?? "").trim(), display_name: String(displayName ?? "").trim() || null }),
  });
}

export function deleteMomRecipient(recipientId) {
  if (!Number.isInteger(Number(recipientId)) || Number(recipientId) <= 0) {
    throw new TypeError("A valid recipient ID is required.");
  }
  return request(`/workflows/recipients/${encodeURIComponent(recipientId)}`, {
    method: "DELETE",
  });
}

export function getMeetings() {
  return request("/meetings/");
}

export function getMeetingDetails(id) {
  if (id === undefined || id === null || String(id).trim() === "") {
    throw new TypeError("A meeting ID is required.");
  }
  return request(`/meetings/${encodeURIComponent(id)}`);
}

export function deleteMeeting(id) {
  if (!Number.isInteger(Number(id)) || Number(id) <= 0) {
    throw new TypeError("A valid meeting ID is required.");
  }
  return request(`/meetings/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function updateSpeakerIdentity(meetingId, speakerId, name) {
  if (!Number.isInteger(Number(meetingId)) || Number(meetingId) <= 0) {
    throw new TypeError("A valid meeting ID is required.");
  }
  const normalizedSpeakerId = String(speakerId ?? "").trim();
  if (!normalizedSpeakerId) {
    throw new TypeError("A speaker ID is required.");
  }
  const normalizedName = String(name ?? "").trim() || null;
  return request(
    `/meetings/${encodeURIComponent(meetingId)}/speakers/${encodeURIComponent(normalizedSpeakerId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: normalizedName }),
    },
  );
}

export function inferSpeakerIdentities(meetingId) {
  if (!Number.isInteger(Number(meetingId)) || Number(meetingId) <= 0) {
    throw new TypeError("A valid meeting ID is required.");
  }
  return request(`/meetings/${encodeURIComponent(meetingId)}/speakers/infer`, {
    method: "POST",
  });
}

export function queryRAG(query) {
  const normalizedQuery = String(query ?? "").trim();
  if (!normalizedQuery) {
    throw new TypeError("Enter a question to search meeting memory.");
  }
  return request("/rag/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: normalizedQuery }),
  });
}

export function sendMomEmail(meetingId, recipients) {
  if (!Number.isInteger(Number(meetingId)) || Number(meetingId) <= 0) {
    throw new TypeError("A valid meeting ID is required.");
  }
  if (!Array.isArray(recipients) || recipients.length === 0) {
    throw new TypeError("Enter at least one email recipient.");
  }
  return request(`/workflows/send-mom/${encodeURIComponent(meetingId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipients }),
  });
}

export function exportToGithub(actionItemId, repoUrl) {
  return request("/workflows/export/github", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_item_id: actionItemId, repo_url: repoUrl }),
  });
}

export function exportToJira(actionItemId) {
  if (!Number.isInteger(Number(actionItemId)) || Number(actionItemId) <= 0) {
    throw new TypeError("A valid action item ID is required.");
  }
  return request("/workflows/export/jira", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_item_id: Number(actionItemId) }),
  });
}

export function getActionItemDrift() {
  return request("/follow-through/drift");
}

export function getReminderStatus() {
  return request("/follow-through/reminders/status");
}

export function getFollowthroughTasks() {
  return request("/follow-through/tasks");
}

export function getFollowthroughRisks() {
  return request("/follow-through/risks");
}

export function updateActionItem(actionItemId, changes) {
  if (!Number.isInteger(Number(actionItemId)) || Number(actionItemId) <= 0) {
    throw new TypeError("A valid action item ID is required.");
  }
  return request(`/follow-through/tasks/${encodeURIComponent(actionItemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
}

export function updateDriftReview(driftId, reviewStatus) {
  if (!Number.isInteger(Number(driftId)) || Number(driftId) <= 0) {
    throw new TypeError("A valid drift observation ID is required.");
  }
  if (!["open", "reviewed", "carried_forward"].includes(reviewStatus)) {
    throw new TypeError("Drift review status must be open, reviewed, or carried_forward.");
  }
  return request(`/follow-through/drift/${encodeURIComponent(driftId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: reviewStatus }),
  });
}

export function setFollowthroughRiskStatus(meetingId, riskIndex, status) {
  if (!Number.isInteger(Number(meetingId)) || Number(meetingId) <= 0 || !Number.isInteger(Number(riskIndex)) || Number(riskIndex) < 0) {
    throw new TypeError("A valid meeting and risk ID are required.");
  }
  if (!["open", "resolved"].includes(status)) {
    throw new TypeError("Risk status must be open or resolved.");
  }
  return request(`/follow-through/risks/${encodeURIComponent(meetingId)}/${encodeURIComponent(riskIndex)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

export function getCalendarConnections() {
  return request("/calendar/connections");
}

export function getCalendarEvents(provider) {
  if (!["google", "microsoft"].includes(provider)) {
    throw new TypeError("Calendar provider must be google or microsoft.");
  }
  return request(`/calendar/${provider}/events`);
}

export function connectCalendar(provider) {
  if (!["google", "microsoft"].includes(provider)) {
    throw new TypeError("Calendar provider must be google or microsoft.");
  }
  window.location.assign(`${API_BASE}/calendar/${provider}/connect`);
}

export function disconnectCalendar(provider) {
  if (!["google", "microsoft"].includes(provider)) {
    throw new TypeError("Calendar provider must be google or microsoft.");
  }
  return request(`/calendar/${provider}/connection`, { method: "DELETE" });
}

export function syncTaskToCalendar(provider, actionItemId) {
  if (!["google", "microsoft"].includes(provider)) {
    throw new TypeError("Calendar provider must be google or microsoft.");
  }
  if (!Number.isInteger(Number(actionItemId)) || Number(actionItemId) <= 0) {
    throw new TypeError("A valid action item ID is required.");
  }
  return request(`/calendar/${provider}/tasks/${encodeURIComponent(actionItemId)}`, {
    method: "POST",
  });
}
