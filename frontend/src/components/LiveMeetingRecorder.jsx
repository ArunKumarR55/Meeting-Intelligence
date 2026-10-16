import { useEffect, useRef, useState } from "react";
import { getAssemblyAIToken, processLiveMeeting } from "../lib/api";

const SAMPLE_RATE = 16_000;
const STREAM_URL = "wss://streaming.assemblyai.com/v3/ws";
const ACTIVE_STATES = new Set(["requesting", "connecting", "recording", "stopping", "processing"]);

function friendlySocketError(event) {
  const reason = String(event?.reason || "").trim();
  if (reason) return `AssemblyAI closed the stream: ${reason.slice(0, 240)}`;
  if (event?.code === 1008) return "AssemblyAI rejected the temporary token. Refresh the page and try again.";
  return "The AssemblyAI connection closed unexpectedly. You can retry or use manual audio upload.";
}

function getTurnTimes(turn) {
  const words = Array.isArray(turn.words) ? turn.words : [];
  const firstStart = words.find((word) => Number.isFinite(word.start))?.start;
  const lastEnd = [...words].reverse().find((word) => Number.isFinite(word.end))?.end;
  return {
    start_seconds: Number.isFinite(firstStart) ? Math.max(0, firstStart / 1000) : null,
    end_seconds: Number.isFinite(lastEnd) ? Math.max(0, lastEnd / 1000) : null,
  };
}

export default function LiveMeetingRecorder({
  title,
  recipients,
  recordingConsent,
  onBusyChange,
  onUploaded,
}) {
  const [status, setStatus] = useState("idle");
  const [includeMicrophone, setIncludeMicrophone] = useState(true);
  const [turns, setTurns] = useState([]);
  const [partialTranscript, setPartialTranscript] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const resourcesRef = useRef(null);
  const turnsRef = useRef([]);
  const stateRef = useRef(status);
  const stopRequestedRef = useRef(false);
  const onUploadedRef = useRef(onUploaded);
  const onBusyChangeRef = useRef(onBusyChange);

  useEffect(() => { stateRef.current = status; }, [status]);
  useEffect(() => { onUploadedRef.current = onUploaded; }, [onUploaded]);
  useEffect(() => { onBusyChangeRef.current = onBusyChange; }, [onBusyChange]);
  useEffect(() => { onBusyChangeRef.current?.(ACTIVE_STATES.has(status)); }, [status]);

  useEffect(() => () => {
    const resources = resourcesRef.current;
    if (!resources) return;
    resources.worklet?.port.postMessage({ type: "stop" });
    for (const track of resources.displayStream?.getTracks() ?? []) track.stop();
    for (const track of resources.microphoneStream?.getTracks() ?? []) track.stop();
    if (resources.socket?.readyState === WebSocket.OPEN) {
      try { resources.socket.send(JSON.stringify({ type: "Terminate" })); } catch { /* Socket is closing. */ }
      resources.socket.close(1000, "Recorder unmounted");
    }
    resources.audioContext?.close().catch(() => {});
  }, []);

  function addFinalTurn(turn) {
    const text = String(turn.transcript ?? "").trim();
    if (!text) return;
    const order = Number.isInteger(turn.turn_order) ? turn.turn_order : null;
    const speakerLabel = typeof turn.speaker_label === "string" && turn.speaker_label.trim()
      ? turn.speaker_label.trim()
      : null;
    const times = getTurnTimes(turn);
    const segment = {
      id: order === null ? `${Date.now()}-${text}` : `turn-${order}`,
      turn_order: order,
      text,
      speaker_label: speakerLabel,
      ...times,
    };
    if (turnsRef.current.some((item) => item.id === segment.id)) return;
    turnsRef.current = [...turnsRef.current, segment].sort((left, right) => {
      if (left.turn_order === null) return 1;
      if (right.turn_order === null) return -1;
      return left.turn_order - right.turn_order;
    });
    setTurns(turnsRef.current);
    setPartialTranscript("");
  }

  function parseAssemblyMessage(event, resources) {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    if (data.type === "Begin") {
      resources.sessionBegun = true;
      resources.resolveBegin?.();
      resources.resolveBegin = null;
      setMessage("Connected. Streaming meeting audio for transcription.");
      return;
    }
    if (data.type === "Turn") {
      const text = String(data.transcript ?? "").trim();
      if (data.end_of_turn === true) addFinalTurn(data);
      else setPartialTranscript(text);
      return;
    }
    if (data.type === "Error") {
      const detail = String(data.error ?? data.message ?? "The transcription provider reported an error.");
      resources.rejectBegin?.(new Error(detail.slice(0, 300)));
      resources.rejectBegin = null;
      setError(detail.slice(0, 300));
      if (stateRef.current === "recording") {
        stopRequestedRef.current = true;
        setStatus("failed");
        void releaseCapture(resources, { terminate: false });
      }
      return;
    }
    if (data.type === "Termination") {
      resources.resolveTermination?.();
      resources.resolveTermination = null;
      return;
    }
  }

  async function releaseCapture(resources, { terminate = true } = {}) {
    if (!resources) return;
    if (resources.worklet && !resources.workletStopped) {
      await new Promise((resolve) => {
        const timer = window.setTimeout(resolve, 1_000);
        resources.resolveWorkletStop = () => {
          window.clearTimeout(timer);
          resources.workletStopped = true;
          resolve();
        };
        resources.worklet.port.postMessage({ type: "stop" });
      });
    }
    for (const source of resources.sources ?? []) {
      try { source.disconnect(); } catch { /* Already disconnected. */ }
    }
    try { resources.worklet?.disconnect(); } catch { /* Already disconnected. */ }
    for (const track of resources.displayStream?.getTracks() ?? []) track.stop();
    for (const track of resources.microphoneStream?.getTracks() ?? []) track.stop();

    const socket = resources.socket;
    if (terminate && socket?.readyState === WebSocket.OPEN) {
      try {
        const drained = new Promise((resolve) => {
          const timer = window.setTimeout(resolve, 10_000);
          resources.resolveTermination = () => {
            window.clearTimeout(timer);
            resolve();
          };
          socket.addEventListener("close", () => {
            window.clearTimeout(timer);
            resolve();
          }, { once: true });
        });
        socket.send(JSON.stringify({ type: "Terminate" }));
        await drained;
      } catch { /* The backend still receives any finalized turns already delivered. */ }
    }
    if (socket && socket.readyState !== WebSocket.CLOSED) {
      try { socket.close(1000, "Meeting capture stopped"); } catch { /* Socket already closed. */ }
    }
    try { await resources.audioContext?.close(); } catch { /* Context already closed. */ }
    if (resourcesRef.current === resources) resourcesRef.current = null;
  }

  async function handleStart() {
    if (!String(title ?? "").trim()) {
      setError("Add a meeting title before recording.");
      return;
    }
    if (!recordingConsent) {
      setError("Confirm recording permission and participant notification before continuing.");
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia || !window.AudioWorkletNode) {
      setError("This browser does not support tab audio capture. Use a current Chrome or Edge browser, or upload a recording instead.");
      return;
    }

    setError("");
    setMessage("");
    setTurns([]);
    turnsRef.current = [];
    setPartialTranscript("");
    stopRequestedRef.current = false;
    setStatus("requesting");

    let resources = null;
    try {
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
      resources = { displayStream, microphoneStream: null, audioContext: null, socket: null, worklet: null, sources: [], sessionBegun: false };
      resourcesRef.current = resources;
      for (const track of displayStream.getTracks()) {
        track.addEventListener("ended", () => {
          if (!stopRequestedRef.current && stateRef.current === "recording") {
            stopRequestedRef.current = true;
            setStatus("failed");
            setError("Screen or tab sharing ended. Start a new capture or use manual audio upload.");
            void releaseCapture(resources, { terminate: true });
          }
        }, { once: true });
      }
      const displayAudioTracks = displayStream.getAudioTracks();
      if (displayAudioTracks.length === 0) {
        throw new Error("No shared-tab audio was provided. In the browser sharing dialog, select the meeting tab and enable Share tab audio.");
      }
      if (includeMicrophone) {
        resources.microphoneStream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false,
        });
      }
      setStatus("connecting");

      const tokenResult = await getAssemblyAIToken();
      if (typeof tokenResult?.token !== "string" || !tokenResult.token) {
        throw new Error("The backend did not return an AssemblyAI streaming token.");
      }
      const params = new URLSearchParams({
        sample_rate: String(SAMPLE_RATE),
        speech_model: "u3-rt-pro",
        speaker_labels: "true",
        token: tokenResult.token,
      });
      const socket = new WebSocket(`${STREAM_URL}?${params.toString()}`);
      resources.socket = socket;
      const begin = new Promise((resolve, reject) => {
        resources.resolveBegin = resolve;
        resources.rejectBegin = reject;
        const timer = window.setTimeout(() => reject(new Error("Timed out while starting the AssemblyAI session.")), 15_000);
        resources.resolveBegin = () => { window.clearTimeout(timer); resolve(); };
        resources.rejectBegin = (reason) => { window.clearTimeout(timer); reject(reason); };
      });
      socket.addEventListener("message", (event) => parseAssemblyMessage(event, resources));
      socket.addEventListener("error", () => {
        resources.rejectBegin?.(new Error("Could not establish the AssemblyAI WebSocket. Check the network and temporary token."));
        resources.rejectBegin = null;
        if (!stopRequestedRef.current) setError("The AssemblyAI WebSocket encountered a network error. You can retry or use manual upload.");
      });
      socket.addEventListener("close", (event) => {
        resources.rejectBegin?.(new Error(friendlySocketError(event)));
        resources.rejectBegin = null;
        resources.resolveTermination?.();
        resources.resolveTermination = null;
        if (!stopRequestedRef.current && stateRef.current === "recording") {
          setStatus("failed");
          setError(friendlySocketError(event));
          void releaseCapture(resources, { terminate: false });
        }
      });
      await new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error("Timed out connecting to AssemblyAI.")), 15_000);
        socket.addEventListener("open", () => { window.clearTimeout(timer); resolve(); }, { once: true });
        socket.addEventListener("error", () => { window.clearTimeout(timer); reject(new Error("Could not connect to AssemblyAI. Check provider availability and browser network access.")); }, { once: true });
        socket.addEventListener("close", (event) => { window.clearTimeout(timer); reject(new Error(friendlySocketError(event))); }, { once: true });
      });

      const audioContext = new AudioContext();
      resources.audioContext = audioContext;
      await audioContext.audioWorklet.addModule("/pcm16-worklet.js");
      const worklet = new AudioWorkletNode(audioContext, "amip-pcm16-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
        channelCountMode: "max",
      });
      resources.worklet = worklet;
      const mutedOutput = audioContext.createGain();
      mutedOutput.gain.value = 0;
      worklet.connect(mutedOutput).connect(audioContext.destination);
      const displaySource = audioContext.createMediaStreamSource(new MediaStream(displayAudioTracks));
      displaySource.connect(worklet);
      resources.sources.push(displaySource);
      if (resources.microphoneStream) {
        const microphoneSource = audioContext.createMediaStreamSource(resources.microphoneStream);
        microphoneSource.connect(worklet);
        resources.sources.push(microphoneSource);
      }
      worklet.port.onmessage = (event) => {
        if (event.data?.type === "stopped") {
          resources.resolveWorkletStop?.();
          resources.resolveWorkletStop = null;
          return;
        }
        const currentSocket = resources.socket;
        if (event.data?.type !== "pcm16" || !currentSocket || currentSocket.readyState !== WebSocket.OPEN || !resources.sessionBegun) return;
        currentSocket.send(event.data.buffer);
      };
      await audioContext.resume();
      await begin;
      setStatus("recording");
      setMessage("Recording the selected meeting tab. Keep this dashboard open until you stop and process.");
    } catch (startError) {
      stopRequestedRef.current = true;
      await releaseCapture(resources, { terminate: true });
      setStatus("failed");
      setError(startError.message || "Could not start live transcription. Try again or upload a recording.");
    }
  }

  async function handleStopAndProcess() {
    if (status !== "recording") return;
    stopRequestedRef.current = true;
    setStatus("stopping");
    setError("");
    setMessage("Finishing the AssemblyAI stream and collecting the final transcript…");
    await releaseCapture(resourcesRef.current, { terminate: true });

    await submitFinalizedTurns(turnsRef.current);
  }

  async function submitFinalizedTurns(finalTurns) {
    const transcript = finalTurns
      .map((turn) => turn.speaker_label ? `${turn.speaker_label}: ${turn.text}` : turn.text)
      .join("\n")
      .trim();
    if (!transcript) {
      setStatus("failed");
      setError("No speech was transcribed. Confirm the selected tab is sharing audio and use manual upload if needed.");
      return;
    }

    setStatus("processing");
    setMessage("Sending the transcript through Gemini analysis, meeting memory, and selected MOM delivery.");
    try {
      const result = await processLiveMeeting({
        title,
        recipients,
        transcript,
        transcriptSegments: finalTurns.map(({ text, speaker_label, start_seconds, end_seconds }) => ({
          text,
          speaker_label,
          start_seconds,
          end_seconds,
        })),
      });
      setStatus("completed");
      setMessage(`Meeting #${result.meeting_id} was processed and added to your library.`);
      await onUploadedRef.current?.(result);
    } catch (processError) {
      setStatus("failed");
      setError(processError.message || "Meeting transcript processing failed. Your live transcript remains available above; you can retry it or use manual upload.");
    }
  }

  async function retryTranscriptProcessing() {
    if (turnsRef.current.length === 0) return;
    setError("");
    await submitFinalizedTurns(turnsRef.current);
  }

  function resetRecorder() {
    setStatus("idle");
    setTurns([]);
    turnsRef.current = [];
    setPartialTranscript("");
    setError("");
    setMessage("");
    stopRequestedRef.current = false;
  }

  return (
    <div className="mt-4">
      <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-600">
        <input
          type="checkbox"
          checked={includeMicrophone}
          onChange={(event) => setIncludeMicrophone(event.target.checked)}
          disabled={ACTIVE_STATES.has(status)}
          className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
        />
        Include my microphone so my voice is transcribed too
      </label>

      {status === "idle" || status === "failed" ? (
        <button
          type="button"
          onClick={() => void handleStart()}
          disabled={!String(title ?? "").trim() || !recordingConsent}
          className="mt-4 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          Start live meeting analysis <span aria-hidden="true">●</span>
        </button>
      ) : status === "recording" ? (
        <button
          type="button"
          onClick={() => void handleStopAndProcess()}
          className="mt-4 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-rose-700"
        >
          Stop &amp; Analyze meeting
        </button>
      ) : ACTIVE_STATES.has(status) ? (
        <div className="mt-4 flex h-12 items-center justify-center gap-2 rounded-xl bg-indigo-50 text-sm font-semibold text-indigo-800" role="status" aria-live="polite">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-300 border-t-indigo-700" aria-hidden="true" />
          {status === "requesting" ? "Choose the meeting tab and share its audio…" : status === "connecting" ? "Connecting to live transcription…" : status === "recording" ? "Recording meeting audio…" : status === "stopping" ? "Finalizing transcript…" : "Analyzing meeting transcript…"}
        </div>
      ) : null}

      {status === "recording" && <div className="mt-3 flex items-center gap-2 text-xs font-medium text-rose-700"><span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" />Live transcription active</div>}
      {message && <p className="mt-3 text-xs leading-5 text-slate-500" aria-live="polite">{message}</p>}
      {error && <p role="alert" className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}
      {(turns.length > 0 || partialTranscript) && <div className="mt-4 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3" aria-live="polite">
        <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Live transcript · {turns.length} finalized turns</p>
        <div className="mt-2 space-y-2">
          {turns.map((turn) => <p key={turn.id} className="whitespace-pre-wrap text-xs leading-5 text-slate-700"><span className="font-semibold text-indigo-700">{turn.speaker_label || "Speaker"}: </span>{turn.text}</p>)}
          {partialTranscript && <p className="text-xs italic leading-5 text-slate-500">{partialTranscript}</p>}
        </div>
      </div>}
      {(status === "completed" || status === "failed") && turns.length > 0 && <button type="button" onClick={resetRecorder} className="mt-3 h-9 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50">Start another recording</button>}
      {status === "failed" && turns.length > 0 && <button type="button" onClick={() => void retryTranscriptProcessing()} className="ml-2 mt-3 h-9 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-xs font-semibold text-indigo-700 hover:bg-indigo-100">Retry transcript processing</button>}
    </div>
  );
}
