import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Pause, Play, Square, Trash2, Check } from "lucide-react";
import { Alert, Button } from "./ui";
import { fmtDuration } from "../lib/format";

const CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
/** Choose a recording format this browser supports. The backend converts all of these with FFmpeg. */
export function pickMime(isSupported: (m: string) => boolean): string | undefined {
  return CANDIDATES.find((m) => isSupported(m));
}
export function extForMime(mime: string): string {
  const base = mime.split(";")[0];
  return base === "audio/webm" ? ".webm" : base === "audio/ogg" ? ".ogg" : base === "audio/mp4" ? ".m4a" : ".webm";
}

type State = "idle" | "requesting" | "recording" | "paused" | "recorded";
export default function Recorder({ onComplete, disabled, label = "Use this recording" }: { onComplete: (blob: Blob, filename: string, seconds: number) => void; disabled?: boolean; label?: string }) {
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const mimeRef = useRef("audio/webm");
  const blobRef = useRef<Blob | null>(null);
  const timer = useRef<number | null>(null);
  const startedAt = useRef(0);
  const accumulated = useRef(0);

  const supported = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

  const stopTimer = () => { if (timer.current) window.clearInterval(timer.current); timer.current = null; };
  const startTimer = () => {
    startedAt.current = Date.now();
    timer.current = window.setInterval(() => setElapsed(accumulated.current + (Date.now() - startedAt.current) / 1000), 250);
  };
  const releaseMic = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };
  useEffect(() => () => { stopTimer(); releaseMic(); if (previewUrl) URL.revokeObjectURL(previewUrl); }, []); // eslint-disable-line

  const start = useCallback(async () => {
    setError(null); setState("requesting");
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const name = (e as DOMException).name;
      setError(name === "NotAllowedError" ? "Microphone permission was denied. Allow microphone access in your browser's address bar and try again, or upload an existing recording instead."
        : name === "NotFoundError" ? "No microphone was found on this device." : "The microphone could not be started.");
      setState("idle"); return;
    }
    const mime = pickMime((m) => MediaRecorder.isTypeSupported(m));
    mimeRef.current = mime ?? "audio/webm";
    const r = new MediaRecorder(stream.current, mime ? { mimeType: mime } : undefined);
    chunks.current = []; accumulated.current = 0; setElapsed(0);
    r.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
    r.onstop = () => {
      const b = new Blob(chunks.current, { type: r.mimeType || mimeRef.current });
      blobRef.current = b; mimeRef.current = r.mimeType || mimeRef.current;
      setPreviewUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(b); });
      releaseMic(); setState("recorded");
    };
    rec.current = r; r.start(1000); startTimer(); setState("recording");
  }, []);

  const pause = () => { rec.current?.pause(); accumulated.current += (Date.now() - startedAt.current) / 1000; stopTimer(); setState("paused"); };
  const resume = () => { rec.current?.resume(); startTimer(); setState("recording"); };
  const stop = () => { if (state === "recording") accumulated.current += (Date.now() - startedAt.current) / 1000; stopTimer(); setElapsed(accumulated.current); rec.current?.stop(); };
  const discard = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); setPreviewUrl(null); blobRef.current = null; setElapsed(0); setState("idle"); };
  const save = () => { if (blobRef.current) onComplete(blobRef.current, `recording-${Date.now()}${extForMime(mimeRef.current)}`, elapsed); };

  if (!supported) return <Alert kind="warn" title="Recording is not available in this browser">Please upload an existing audio file instead, or open HeritageLens in a current version of Chrome, Edge, Firefox or Safari over HTTPS or localhost.</Alert>;
  return (
    <div className="rounded-2xl border border-line bg-white p-5">
      <div className="flex flex-wrap items-center gap-4">
        <div className={`flex h-14 w-14 items-center justify-center rounded-full ${state === "recording" ? "bg-red-600 text-white motion-safe:animate-pulse" : "bg-clay-pale text-clay-deep"}`} aria-hidden><Mic className="h-6 w-6" /></div>
        <div>
          <p className="font-serif text-3xl tabular-nums" aria-hidden>{fmtDuration(elapsed)}</p>
          <p className="text-sm" role="status" aria-live="polite">{{ idle: "Ready to record", requesting: "Waiting for microphone permission…", recording: "Recording…", paused: "Paused", recorded: "Recording finished. Listen, then save or re-record." }[state]}</p>
        </div>
      </div>
      {error && <div className="mt-4"><Alert kind="error">{error}</Alert></div>}
      <div className="mt-4 flex flex-wrap gap-2">
        {(state === "idle" || state === "requesting") && <Button onClick={start} loading={state === "requesting"} disabled={disabled}><Mic className="h-4 w-4" aria-hidden /> Start recording</Button>}
        {state === "recording" && <Button variant="secondary" onClick={pause}><Pause className="h-4 w-4" aria-hidden /> Pause</Button>}
        {state === "paused" && <Button variant="secondary" onClick={resume}><Play className="h-4 w-4" aria-hidden /> Resume</Button>}
        {(state === "recording" || state === "paused") && <Button onClick={stop}><Square className="h-4 w-4" aria-hidden /> Stop</Button>}
        {state === "recorded" && <>
          <Button onClick={save} disabled={disabled}><Check className="h-4 w-4" aria-hidden /> {label}</Button>
          <Button variant="secondary" onClick={() => { discard(); void start(); }}><Mic className="h-4 w-4" aria-hidden /> Re-record</Button>
          <Button variant="danger" onClick={discard}><Trash2 className="h-4 w-4" aria-hidden /> Delete</Button></>}
      </div>
      {state === "recorded" && previewUrl && <audio controls src={previewUrl} className="mt-4 w-full" aria-label="Recording preview" />}
    </div>
  );
}
