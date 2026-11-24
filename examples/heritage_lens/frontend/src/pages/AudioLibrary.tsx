import { useMemo, useState } from "react";
import { AudioLines, Mic, Trash2 } from "lucide-react";
import AudioPlayer from "../components/AudioPlayer";
import FileDrop from "../components/FileDrop";
import Recorder from "../components/Recorder";
import { Alert, Button, Container, EmptyState, ErrorAlert, Field, LiveGate, PageHeader, ProvenanceBadge, Select, Spinner, TextArea, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { langName, languageOptions, useCapabilities } from "../lib/caps";
import { fmtBytes, fmtDate, fmtDuration } from "../lib/format";
import type { Audio } from "../lib/types";

function Item({ a, onChanged }: { a: Audio; onChanged: () => void }) {
  const { caps } = useCapabilities();
  const langs = caps?.services["indic-transcribe"].languages ?? {};
  const speak = caps?.services["indic-speak"];
  const [lang, setLang] = useState(a.language && a.language in langs ? a.language : "te");
  const [text, setText] = useState(a.transcript_edited ?? a.transcript ?? "");
  const [sLang, setSLang] = useState(a.language && speak && a.language in speak.languages ? a.language : "te");
  const [voice, setVoice] = useState("Sravani");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [ok, setOk] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, msg?: string) => { setBusy(true); setErr(null); setOk(null); try { await fn(); if (msg) setOk(msg); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const original = a.kind === "original";
  return (
    <li className="card p-5">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm"><strong>{original ? a.original_filename : `AI narration (${a.narration_voice})`}</strong><ProvenanceBadge value={a.provenance} /><span className="text-mute">{langName(a.language, caps)} · {fmtDuration(a.duration)} · {fmtBytes(a.size_bytes)} · {fmtDate(a.created_at)}</span></div>
      <AudioPlayer src={api.audioFile(a.id)} title={original ? a.original_filename ?? "Recording" : "AI narration"} knownDuration={a.duration} />
      {!original && a.narrated_text && <details className="mt-2 text-sm"><summary className="cursor-pointer font-semibold">Text that was read aloud</summary><p className="indic mt-1 whitespace-pre-wrap">{a.narrated_text}</p></details>}
      {original && (
        <details className="mt-3"><summary className="cursor-pointer text-sm font-semibold">Transcript and narration</summary>
          <div className="mt-3 space-y-4">
            <LiveGate what="Transcription"><div className="flex flex-wrap items-end gap-3"><Field label="Language spoken">{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value)}>{Object.entries(langs).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
              <Button loading={busy} onClick={() => void run(async () => { const r = await api.transcribe({ audio_id: a.id, language: lang, force: !!a.transcript }); setText(r.transcript_edited ?? r.transcript ?? ""); }, "Transcribed.")}>{a.transcript ? "Transcribe again" : "Transcribe"}</Button></div></LiveGate>
            {(a.transcript || text) && <div><div className="mb-1 flex items-center gap-2"><span className="label !mb-0">Transcript</span><ProvenanceBadge value={a.transcript_provenance} /></div><TextArea rows={6} value={text} onChange={(e) => setText(e.target.value)} aria-label="Transcript" />
              <div className="mt-2 flex flex-wrap gap-2"><Button variant="secondary" loading={busy} onClick={() => void run(() => api.audioEditTranscript(a.id, text), "Corrections saved; the raw transcript is kept separately.")}>Save corrections</Button></div></div>}
            {text.trim() && <LiveGate what="Speech generation"><div className="grid gap-3 sm:grid-cols-3 sm:items-end"><Field label="Narrate in">{(id) => <Select id={id} value={sLang} onChange={(e) => setSLang(e.target.value)}>{Object.entries(speak?.languages ?? {}).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
              <Field label="Voice">{(id) => <Select id={id} value={voice} onChange={(e) => setVoice(e.target.value)}>{Object.entries(speak?.voices ?? {}).map(([k, v]) => <option key={k} value={k}>{k} ({v.gender})</option>)}</Select>}</Field>
              <Button loading={busy} onClick={() => void run(() => api.speak({ story_id: a.story_id ?? undefined, text, language: sLang, voice }), "Narration generated. Find it under “Generated narration”; your original is untouched.")}>Generate speech</Button></div></LiveGate>}
          </div></details>)}
      {err && <div className="mt-3"><Alert kind="error">{err}</Alert></div>}{ok && <div className="mt-3"><Alert kind="success">{ok}</Alert></div>}
      <div className="mt-3 text-right"><button className="inline-flex items-center gap-1 text-xs font-semibold text-red-800 underline" onClick={() => { if (window.confirm(original ? "Delete this ORIGINAL recording permanently?" : "Delete this narration?")) void run(() => api.audioDelete(a.id)); }}><Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete</button></div>
    </li>
  );
}

export default function AudioLibrary() {
  const { caps } = useCapabilities();
  const [tab, setTab] = useState<"original" | "generated">("original");
  const list = useAsync(() => api.audioList({ kind: tab }), [tab]);
  const [q, setQ] = useState(""); const [lang, setLang] = useState("");
  const [progress, setProgress] = useState<number | null>(null); const [upLang, setUpLang] = useState("te");
  const [err, setErr] = useState<string | null>(null); const [ok, setOk] = useState<string | null>(null);
  const send = async (b: Blob, name: string) => { setErr(null); setOk(null); setProgress(0); try { await api.audioUpload(b, name, { language: upLang }, setProgress); setOk("Saved. The original recording is preserved."); setTab("original"); list.reload(); } catch (e) { setErr((e as Error).message); } finally { setProgress(null); } };
  const items = useMemo(() => (list.data?.items ?? []).filter((a) => (!lang || a.language === lang) && (!q || `${a.original_filename ?? ""} ${a.transcript ?? ""} ${a.transcript_edited ?? ""} ${a.narrated_text ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [list.data, q, lang]);
  return (
    <>
      <PageHeader eyebrow="Audio Heritage Library" title="Make memories listenable">Original recordings and AI-generated narration are kept apart, so a human voice is never mistaken for a synthetic one.</PageHeader>
      <Container className="space-y-8">
        <section className="card p-6" aria-labelledby="add"><h2 id="add" className="flex items-center gap-2 text-xl"><Mic className="h-5 w-5" aria-hidden /> Add a recording</h2>
          <div className="mt-3 max-w-xs"><Field label="Language spoken">{(id) => <Select id={id} value={upLang} onChange={(e) => setUpLang(e.target.value)}>{languageOptions(caps).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field></div>
          <div className="mt-4 grid gap-5 lg:grid-cols-2"><Recorder onComplete={(b, n) => void send(b, n)} disabled={progress !== null} label="Save recording" />
            <FileDrop accept="audio/*,.wav,.mp3,.flac,.ogg,.webm,.m4a" extensions={[".wav", ".mp3", ".flac", ".ogg", ".webm", ".m4a"]} maxMB={50} title="Or upload an audio file" progress={progress} disabled={progress !== null} onFile={(f) => void send(f, f.name)} /></div>
          {err && <div className="mt-3"><Alert kind="error">{err}</Alert></div>}{ok && <div className="mt-3"><Alert kind="success">{ok}</Alert></div>}</section>
        <section aria-labelledby="lib">
          <div className="flex flex-wrap items-end justify-between gap-4"><div role="tablist" aria-label="Library" className="flex gap-2">{(["original", "generated"] as const).map((t) => <button id={`tab-${t}`} key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded-full border px-4 py-2 text-sm font-semibold ${tab === t ? "border-clay bg-clay text-white" : "border-clay-muted bg-white"}`}>{t === "original" ? "Original recordings" : "Generated narration"}</button>)}</div>
            <div className="flex flex-wrap gap-3"><div className="w-56"><Field label="Search">{(id) => <TextInput id={id} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or transcript" />}</Field></div>
              <div className="w-44"><Field label="Language">{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value)}><option value="">All</option>{[...new Set((list.data?.items ?? []).map((a) => a.language).filter(Boolean) as string[])].map((c) => <option key={c} value={c}>{langName(c, caps)}</option>)}</Select>}</Field></div></div></div>
          <div className="mt-5" role="tabpanel" aria-labelledby={`tab-${tab}`}>
            {list.loading && <Spinner />}<ErrorAlert error={list.error} onRetry={list.reload} />
            {!list.loading && items.length === 0 && <EmptyState icon={<AudioLines className="h-8 w-8" />} title={tab === "original" ? "No recordings yet" : "No narration yet"}>{tab === "original" ? "Record or upload your first recording above." : "Open a recording or a story, write or transcribe its text, then generate speech."}</EmptyState>}
            <ul className="space-y-4">{items.map((a) => <Item key={a.id} a={a} onChanged={list.reload} />)}</ul></div></section>
      </Container>
    </>
  );
}
