import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Mic, PenLine, Trash2, Upload } from "lucide-react";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { useCapabilities, langName } from "../lib/caps";
import { fmtDuration } from "../lib/format";
import type { Audio, Story, Translation, Transliteration } from "../lib/types";
import AudioPlayer from "./AudioPlayer";
import FileDrop from "./FileDrop";
import Recorder from "./Recorder";
import { Alert, Button, EmptyState, ErrorAlert, Field, LiveGate, ProvenanceBadge, Select, Spinner, TextArea, TextInput } from "./ui";

interface StepProps { story: Story; onStory: (s: Story) => void }
const useBusy = () => {
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [ok, setOk] = useState<string | null>(null);
  const run = async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setBusy(true); setError(null); setOk(null);
    try { const r = await fn(); if (success) setOk(success); return r; } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return { busy, error, ok, run, setError };
};
const Msg = ({ error, ok }: { error: string | null; ok: string | null }) => <>{error && <div className="mt-3"><Alert kind="error">{error}</Alert></div>}{ok && <div className="mt-3"><Alert kind="success">{ok}</Alert></div>}</>;

// ------------------------------------------------------------------ Step 2: contribute
export function ContributeStep({ story, onStory }: StepProps) {
  const [mode, setMode] = useState<"record" | "upload" | "write">("record");
  const audio = useAsync(() => api.audioList({ story_id: Number(story.id), kind: "original" }), [story.id]);
  const [progress, setProgress] = useState<number | null>(null);
  const [text, setText] = useState(story.original_text ?? "");
  const t = useBusy();
  const send = (blob: Blob, name: string) => t.run(async () => { setProgress(0); try { await api.audioUpload(blob, name, { story_id: Number(story.id), language: story.language ?? undefined }, setProgress); } finally { setProgress(null); } audio.reload(); }, "Your original recording was saved. It will never be changed.");
  const tabs = [["record", Mic, "Record my story"], ["upload", Upload, "Upload a recording"], ["write", PenLine, "Write my story"]] as const;
  return (
    <div className="space-y-6">
      <div role="tablist" aria-label="How to contribute" className="grid gap-3 sm:grid-cols-3">
        {tabs.map(([k, I, label]) => <button key={k} role="tab" aria-selected={mode === k} onClick={() => setMode(k)} className={`flex items-center gap-3 rounded-2xl border p-4 text-left font-semibold ${mode === k ? "border-clay bg-clay-pale" : "border-line bg-white hover:bg-sand"}`}><I className="h-5 w-5" aria-hidden /> {label}</button>)}
      </div>
      {mode === "record" && <Recorder onComplete={(b, n) => void send(b, n)} disabled={t.busy} label="Save this recording" />}
      {mode === "upload" && <FileDrop accept="audio/*,.wav,.mp3,.flac,.ogg,.webm,.m4a" extensions={[".wav", ".mp3", ".flac", ".ogg", ".webm", ".m4a"]} maxMB={50} title="Drop an audio file here" progress={progress} disabled={t.busy} onFile={(f) => void send(f, f.name)} />}
      {mode === "write" && (
        <div>
          <Field label="Your story" hint="Write in any language. This is saved as your original text.">{(id) => <TextArea id={id} rows={12} value={text} onChange={(e) => setText(e.target.value)} lang={story.language ?? undefined} />}</Field>
          <div className="mt-2 flex items-center justify-between text-xs"><span>{text.length.toLocaleString()} characters</span>
            <Button loading={t.busy} onClick={() => void t.run(async () => { onStory(await api.updateStory(story.id, { original_text: text })); }, "Draft saved.")}>Save draft</Button></div>
        </div>)}
      <Msg error={t.error} ok={t.ok} />
      <section aria-labelledby="saved-rec"><h3 id="saved-rec" className="text-lg">Original recordings for this story</h3>
        {audio.loading && <Spinner />}<ErrorAlert error={audio.error} onRetry={audio.reload} />
        {audio.data?.items.length === 0 && <p className="mt-2 text-sm">No recordings yet. You can also just write the story.</p>}
        <ul className="mt-3 space-y-3">{audio.data?.items.map((a) => (
          <li key={a.id} className="card p-3"><AudioPlayer src={api.audioFile(a.id)} title={a.original_filename ?? `Recording ${a.id}`} knownDuration={a.duration} />
            <div className="mt-2 flex justify-between text-xs"><span>{fmtDuration(a.duration)} · original preserved</span>
              <button className="font-semibold text-red-800 underline" onClick={() => { if (window.confirm("Delete this original recording permanently?")) void t.run(async () => { await api.audioDelete(a.id); audio.reload(); }); }}>Delete</button></div></li>))}</ul></section>
    </div>
  );
}

// ------------------------------------------------------------------ Step 3: transcription
export function TranscribeStep({ story, onStory }: StepProps) {
  const { caps } = useCapabilities();
  const audio = useAsync(() => api.audioList({ story_id: Number(story.id), kind: "original" }), [story.id]);
  const [sel, setSel] = useState<number | null>(null);
  const rec: Audio | undefined = audio.data?.items.find((a) => a.id === sel) ?? audio.data?.items[0];
  const langs = caps?.services["indic-transcribe"].languages ?? {};
  const [lang, setLang] = useState(story.language ?? "te");
  const [text, setText] = useState("");
  const t = useBusy();
  useEffect(() => { if (rec) setText(rec.transcript_edited ?? rec.transcript ?? ""); }, [rec?.id, rec?.transcript, rec?.transcript_edited]);
  const supported = lang in langs;

  if (audio.loading) return <Spinner />;
  if (!rec) return <EmptyState title="No recording to transcribe">Go back one step to record or upload audio. If you wrote your story, you can skip this step.</EmptyState>;
  const run = () => t.run(async () => { await api.transcribe({ audio_id: rec.id, language: lang, force: !!rec.transcript }); audio.reload(); }, "Transcription finished. Please read it and correct any mistakes.");
  return (
    <div className="space-y-5">
      {audio.data!.items.length > 1 && <Field label="Recording">{(id) => <Select id={id} value={rec.id} onChange={(e) => setSel(Number(e.target.value))}>{audio.data!.items.map((a) => <option key={a.id} value={a.id}>{a.original_filename} ({fmtDuration(a.duration)})</option>)}</Select>}</Field>}
      <AudioPlayer src={api.audioFile(rec.id)} title={rec.original_filename ?? "Recording"} knownDuration={rec.duration} />
      <LiveGate what="Transcription">
        <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field label="Language spoken in the recording" error={supported ? undefined : "This language is not supported for transcription."}>{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value)}>{Object.entries(langs).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
          <Button onClick={() => void run()} loading={t.busy} disabled={!supported}>{rec.transcript ? "Transcribe again" : "Transcribe with Bodhan"}</Button>
        </div>
        <p className="mt-2 text-xs">Recordings longer than 30 seconds are split into ordered segments automatically.</p>
      </LiveGate>
      <Msg error={t.error} ok={t.ok} />
      {(rec.transcript || rec.transcript_edited) && (
        <section aria-labelledby="tr" className="space-y-3">
          <div className="flex flex-wrap items-center gap-2"><h3 id="tr" className="text-lg">Transcript</h3><ProvenanceBadge value={rec.transcript_provenance} />{rec.transcript_segments && <span className="text-xs">{rec.transcript_segments.length} segment(s)</span>}</div>
          <TextArea rows={10} value={text} onChange={(e) => setText(e.target.value)} aria-label="Transcript (editable)" lang={rec.transcript_language ?? undefined} />
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" loading={t.busy} onClick={() => void t.run(async () => { await api.audioEditTranscript(rec.id, text); audio.reload(); }, "Your corrections were saved. The machine transcript is kept separately.")}>Save corrections</Button>
            <Button loading={t.busy} onClick={() => { if (story.original_text && story.original_text !== text && !window.confirm("Replace the story text you already wrote with this transcript?")) return; void t.run(async () => { await api.audioEditTranscript(rec.id, text); onStory(await api.updateStory(story.id, { original_text: text })); audio.reload(); }, "Story text updated from your corrected transcript."); }}>Use as story text</Button></div>
          {rec.transcript && rec.transcript_edited && rec.transcript !== rec.transcript_edited && <details className="text-sm"><summary className="cursor-pointer font-semibold">Show the original machine transcript</summary><p className="indic mt-2 whitespace-pre-line">{rec.transcript}</p></details>}
        </section>)}
    </div>
  );
}

// ------------------------------------------------------------------ Step 4: translate, transliterate, narrate
function TranslationEditor({ tr, onSaved }: { tr: Translation; onSaved: () => void }) {
  const [text, setText] = useState(tr.edited_text ?? tr.translated_text);
  const t = useBusy();
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2"><strong>{langName(tr.target_language)}</strong><ProvenanceBadge value={tr.provenance} />{tr.truncated && <span className="text-xs font-semibold text-red-800">Warning: output was cut short by the service</span>}</div>
      <div className="grid gap-3 md:grid-cols-2">
        <div><p className="label">Original (unchanged)</p><p className="indic max-h-60 overflow-auto whitespace-pre-line rounded-xl bg-sand/60 p-3 text-sm">{tr.original_text}</p></div>
        <div><label className="label" htmlFor={`tr-${tr.id}`}>Translation (editable)</label><TextArea id={`tr-${tr.id}`} rows={6} value={text} onChange={(e) => setText(e.target.value)} /></div></div>
      <div className="mt-2 flex gap-2"><Button variant="secondary" loading={t.busy} onClick={() => void t.run(async () => { await api.editTranslation(tr.id, text); onSaved(); }, "Saved.")}>Save translation</Button>
        <Button variant="ghost" onClick={() => { if (window.confirm("Delete this translation?")) void t.run(async () => { await api.deleteTranslation(tr.id); onSaved(); }); }}><Trash2 className="h-4 w-4" aria-hidden /> Delete</Button></div>
      <Msg error={t.error} ok={t.ok} />
    </div>
  );
}

export function LanguagesStep({ story }: StepProps) {
  const { caps } = useCapabilities();
  const trs = useAsync(() => api.translations(Number(story.id)), [story.id]);
  const gen = useAsync(() => api.audioList({ story_id: Number(story.id), kind: "generated" }), [story.id]);
  const svc = caps?.services;
  const targets = svc?.["indic-translate"].target_languages ?? {};
  const tl = svc?.["indic-transliterate"].languages ?? {};
  const speak = svc?.["indic-speak"];
  const [target, setTarget] = useState("en"); const [script, setScript] = useState("native");
  const [xlang, setXlang] = useState(story.language && story.language in tl ? story.language : "te"); const [xscript, setXscript] = useState("roman");
  const [xresults, setXresults] = useState<Transliteration[]>([]);
  const [sLang, setSLang] = useState(story.language && speak && story.language in speak.languages ? story.language : "te"); const [voice, setVoice] = useState("Sravani"); const [style, setStyle] = useState(""); const [src, setSrc] = useState("story");
  const a = useBusy(), b = useBusy(), c = useBusy();
  const hasText = !!story.original_text?.trim();
  const codemixOk = !!svc && !svc["indic-translate"].codemix_unavailable_for.includes(target) && target !== "en";

  return (
    <div className="space-y-10">
      {!hasText && <Alert kind="warn" title="Your story has no text yet">Translation, transliteration and narration use your story text. Write it or transcribe a recording first.</Alert>}
      <section aria-labelledby="s-tr"><h3 id="s-tr" className="text-xl">Translate</h3>
        <p className="mt-1 text-sm">Translation changes the language. Your original text is never changed.</p>
        <LiveGate what="Translation"><div className="mt-3 grid gap-4 sm:grid-cols-3 sm:items-end">
          <Field label="Translate into">{(id) => <Select id={id} value={target} onChange={(e) => { setTarget(e.target.value); setScript("native"); }}>{Object.entries(targets).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</Select>}</Field>
          <Field label="Writing style">{(id) => <Select id={id} value={script} disabled={target === "en"} onChange={(e) => setScript(e.target.value)}><option value="native">Native script</option><option value="roman">Roman letters</option>{codemixOk && <option value="codemix">Code-mixed</option>}</Select>}</Field>
          <Button loading={a.busy} disabled={!hasText} onClick={() => void a.run(async () => { await api.translate({ story_id: Number(story.id), target_language: target, script, save: true }); trs.reload(); }, "Translation saved as a separate version.")}>Translate</Button></div></LiveGate>
        <p className="mt-1 text-xs">Bhili and Bhojpuri are supported for transcription only, not translation.</p>
        <Msg error={a.error} ok={a.ok} />
        <div className="mt-4 space-y-4">{trs.data?.items.map((t) => <TranslationEditor key={`${t.id}-${t.updated_at}`} tr={t} onSaved={trs.reload} />)}</div></section>

      <section aria-labelledby="s-x"><h3 id="s-x" className="text-xl">Transliterate</h3>
        <p className="mt-1 text-sm">Transliteration keeps the language and changes only the writing system, for example Telugu script to Roman letters.</p>
        <LiveGate what="Transliteration"><div className="mt-3 grid gap-4 sm:grid-cols-3 sm:items-end">
          <Field label="Language of the text">{(id) => <Select id={id} value={xlang} onChange={(e) => setXlang(e.target.value)}>{Object.entries(tl).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</Select>}</Field>
          <Field label="Output">{(id) => <Select id={id} value={xscript} onChange={(e) => setXscript(e.target.value)}><option value="roman">Roman letters</option><option value="native">Native script</option></Select>}</Field>
          <Button loading={b.busy} disabled={!hasText} onClick={() => void b.run(async () => { const r = await api.transliterate({ story_id: Number(story.id), text: story.original_text!, language: xlang, script: xscript, save: true }); if (r.saved) setXresults((x) => [r.saved!, ...x]); }, "Saved as a separate version.")}>Convert</Button></div></LiveGate>
        <Msg error={b.error} ok={b.ok} />
        {xresults.map((x) => <div key={x.id} className="mt-3 grid gap-3 md:grid-cols-2"><div><p className="label">Original</p><p className="indic rounded-xl bg-sand/60 p-3 text-sm">{x.source_text}</p></div><div><p className="label">Converted <ProvenanceBadge value={x.provenance} /></p><p className="indic rounded-xl bg-white p-3 text-sm border border-line">{x.converted_text}</p></div></div>)}</section>

      <section aria-labelledby="s-s"><h3 id="s-s" className="text-xl">Generate narration</h3>
        <p className="mt-1 text-sm">Creates a separate AI-voiced audio file. Your original recording is never replaced.</p>
        <LiveGate what="Speech generation"><div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Text to read">{(id) => <Select id={id} value={src} onChange={(e) => setSrc(e.target.value)}><option value="story">Original story text</option>{trs.data?.items.map((t) => <option key={t.id} value={`t${t.id}`}>{langName(t.target_language)} translation</option>)}</Select>}</Field>
          <Field label="Language">{(id) => <Select id={id} value={sLang} onChange={(e) => setSLang(e.target.value)}>{Object.entries(speak?.languages ?? {}).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</Select>}</Field>
          <Field label="Voice">{(id) => <Select id={id} value={voice} onChange={(e) => setVoice(e.target.value)}>{Object.entries(speak?.voices ?? {}).map(([k, v]) => <option key={k} value={k}>{k} ({v.gender})</option>)}</Select>}</Field>
          <Field label="Style (optional)">{(id) => <Select id={id} value={style} onChange={(e) => setStyle(e.target.value)}><option value="">Default</option>{speak?.styles.map((s) => <option key={s}>{s}</option>)}</Select>}</Field></div>
          <div className="mt-3"><Button loading={c.busy} disabled={!hasText && src === "story"} onClick={() => void c.run(async () => { await api.speak({ story_id: Number(story.id), translation_id: src.startsWith("t") ? Number(src.slice(1)) : undefined, language: sLang, voice, style: style || null }); gen.reload(); }, "Narration generated and saved as a separate recording.")}>Generate narration</Button></div></LiveGate>
        <p className="mt-1 text-xs">Long text is read in sentence-sized parts and joined. Narration is limited to 5,000 characters per request.</p>
        <Msg error={c.error} ok={c.ok} />
        <ul className="mt-4 space-y-3">{gen.data?.items.map((g) => <li key={g.id}><div className="mb-1 flex items-center gap-2 text-xs"><ProvenanceBadge value={g.provenance} /><span>{g.narration_voice} · {langName(g.language)} · {fmtDuration(g.duration)}</span></div><AudioPlayer src={api.audioFile(g.id)} title="AI narration" knownDuration={g.duration} /></li>)}</ul></section>
    </div>
  );
}

// ------------------------------------------------------------------ Step 5: materials
export function MaterialsStep({ story }: StepProps) {
  const imgs = useAsync(() => api.images(Number(story.id)), [story.id]);
  const docs = useAsync(() => api.documents({ story_id: Number(story.id) }), [story.id]);
  const [cap, setCap] = useState(""); const [attr, setAttr] = useState(""); const [dtype, setDtype] = useState("letter");
  const [p1, setP1] = useState<number | null>(null); const [p2, setP2] = useState<number | null>(null);
  const a = useBusy(), b = useBusy();
  return (
    <div className="grid gap-10 lg:grid-cols-2">
      <section aria-labelledby="m-ph"><h3 id="m-ph" className="text-xl">Photographs</h3><p className="mt-1 text-sm">The first photograph becomes the cover.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2"><Field label="Caption">{(id) => <TextInput id={id} value={cap} onChange={(e) => setCap(e.target.value)} maxLength={500} />}</Field><Field label="Credit / attribution">{(id) => <TextInput id={id} value={attr} onChange={(e) => setAttr(e.target.value)} maxLength={300} />}</Field></div>
        <div className="mt-3"><FileDrop accept="image/png,image/jpeg" extensions={[".jpg", ".jpeg", ".png"]} maxMB={15} title="Add a photograph" progress={p1} disabled={a.busy}
          onFile={(f) => void a.run(async () => { setP1(0); try { await api.imageUpload(f, { story_id: Number(story.id), caption: cap || undefined, attribution: attr || undefined }, setP1); } finally { setP1(null); } setCap(""); imgs.reload(); }, "Photograph added.")} /></div>
        <Msg error={a.error} ok={a.ok} />
        <ul className="mt-4 space-y-2">{imgs.data?.items.map((i) => <li key={i.id} className="card flex items-center justify-between gap-3 p-3 text-sm"><span className="min-w-0 truncate">{i.caption || i.original_filename}</span><button className="font-semibold text-red-800 underline" onClick={() => void a.run(async () => { await api.imageDelete(i.id); imgs.reload(); })}>Remove</button></li>)}</ul></section>
      <section aria-labelledby="m-doc"><h3 id="m-doc" className="text-xl">Documents and letters</h3><p className="mt-1 text-sm">Scans stay private to you. Digitise them in the <Link className="underline" to="/digitizer">Document Digitizer</Link>.</p>
        <div className="mt-3"><Field label="Document type">{(id) => <Select id={id} value={dtype} onChange={(e) => setDtype(e.target.value)}>{["letter", "manuscript", "printed", "photograph", "other"].map((d) => <option key={d}>{d}</option>)}</Select>}</Field></div>
        <div className="mt-3"><FileDrop accept="image/png,image/jpeg,application/pdf" extensions={[".pdf", ".jpg", ".jpeg", ".png"]} maxMB={25} title="Add a scanned document" progress={p2} disabled={b.busy}
          onFile={(f) => void b.run(async () => { setP2(0); try { await api.documentUpload(f, { story_id: Number(story.id), document_type: dtype }, setP2); } finally { setP2(null); } docs.reload(); }, "Document stored. The original file is preserved untouched.")} /></div>
        <Msg error={b.error} ok={b.ok} />
        <ul className="mt-4 space-y-2">{docs.data?.items.map((d) => <li key={d.id} className="card p-3 text-sm">{d.original_filename} <span className="text-mute">· {d.page_count ?? 1} page(s) · OCR {d.ocr_status.replace("_", " ")}</span></li>)}</ul></section>
    </div>
  );
}
