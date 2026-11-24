import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Play } from "lucide-react";
import AudioPlayer from "../components/AudioPlayer";
import FileDrop from "../components/FileDrop";
import Recorder from "../components/Recorder";
import { Alert, Button, Container, Field, PageHeader, ProvenanceBadge, Select } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useMode } from "../lib/mode";
import { useCapabilities, langName } from "../lib/caps";
import { DEMO_OCR_TEXT, DEMO_TELUGU_TEXT, DEMO_TRANSLATIONS, DEMO_TRANSLITERATION } from "../lib/demoData";
import { makeSampleLetter } from "../lib/sampleDocument";
import type { Provenance } from "../lib/types";

const INPUT_TELUGU = "నమస్కారం, మా గ్రామానికి స్వాగతం.";

function Example({ id, title, what, why, input, how, expected, children }: { id: string; title: string; what: string; why: string; input: string; how: string; expected: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="card scroll-mt-24 p-6 sm:p-8">
      <h2 id={`${id}-h`} className="text-2xl">{title}</h2>
      <dl className="mt-4 grid gap-x-8 gap-y-3 text-sm md:grid-cols-2">
        {([["What it does", what], ["Why it is useful", why], ["Input needed", input], ["How to try it", how], ["What to expect", expected]] as const).map(([k, v]) => <div key={k}><dt className="font-semibold">{k}</dt><dd className="text-clay-deep/85">{v}</dd></div>)}
      </dl>
      <div className="mt-6 rounded-2xl border border-line bg-sand/50 p-5">{children}</div>
    </section>
  );
}

function Result({ prov, children }: { prov: Provenance; children: ReactNode }) {
  return <div className="mt-4 rounded-xl border border-line bg-white p-4" aria-live="polite"><div className="mb-2"><ProvenanceBadge value={prov} /></div><div className="indic whitespace-pre-wrap">{children}</div></div>;
}

function useRun() {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => { setBusy(true); setErr(null); try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  return { busy, err, run, clear: () => setErr(null) };
}

function NeedLogin({ children }: { children: ReactNode }) {
  const { user } = useAuth(); const { live } = useMode();
  if (live && !user) return <Alert kind="info" title="Sign in to run this live">Live actions run through your account. <Link to="/login" state={{ from: "/try" }} className="font-semibold underline">Sign in</Link> or <Link to="/register" className="font-semibold underline">create an account</Link>. You can still watch the Demo Mode output.</Alert>;
  return <>{children}</>;
}

function TranslateDemo() {
  const { live } = useMode(); const { user } = useAuth(); const { caps } = useCapabilities();
  const [target, setTarget] = useState("en"); const [out, setOut] = useState<{ prov: Provenance; text: string } | null>(null); const r = useRun();
  const targets = Object.entries(caps?.services["indic-translate"].target_languages ?? { en: "English", hi: "Hindi" });
  const go = () => r.run(async () => {
    if (!live) { setOut({ prov: "illustrative", text: DEMO_TRANSLATIONS[target] ?? "No hand-written sample exists for this language in Demo Mode. Switch to Live to translate for real." }); return; }
    const res = await api.translate({ text: INPUT_TELUGU, target_language: target }); setOut({ prov: res.provenance, text: res.translation + (res.truncated ? "\n(Warning: output was cut short)" : "") });
  });
  return (<><p className="label">Input (Telugu)</p><p className="indic card p-4 text-lg">{INPUT_TELUGU}</p>
    <div className="mt-4 flex flex-wrap items-end gap-3"><Field label="Translate into">{(id) => <Select id={id} value={target} onChange={(e) => setTarget(e.target.value)}>{targets.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
      <Button onClick={() => void go()} loading={r.busy} disabled={live && !user}><Play className="h-4 w-4" aria-hidden /> Try translation</Button></div>
    {r.err && <div className="mt-3"><Alert kind="error">{r.err}</Alert></div>}{out && <Result prov={out.prov}>{out.text}</Result>}</>);
}

function TransliterateDemo() {
  const { live } = useMode(); const { user } = useAuth();
  const [script, setScript] = useState("roman"); const [out, setOut] = useState<{ prov: Provenance; text: string } | null>(null); const r = useRun();
  const go = () => r.run(async () => {
    if (!live) { setOut({ prov: "illustrative", text: script === "roman" ? DEMO_TRANSLITERATION : "నమస్కారం (already in native script)" }); return; }
    const res = await api.transliterate({ text: "నమస్కారం", language: "te", script }); setOut({ prov: res.provenance, text: res.converted_text });
  });
  return (<><p className="label">Input (Telugu)</p><p className="indic card p-4 text-lg">నమస్కారం</p>
    <div className="mt-4 flex flex-wrap items-end gap-3"><Field label="Output script">{(id) => <Select id={id} value={script} onChange={(e) => setScript(e.target.value)}><option value="roman">Roman letters</option><option value="native">Native script</option></Select>}</Field>
      <Button onClick={() => void go()} loading={r.busy} disabled={live && !user}><Play className="h-4 w-4" aria-hidden /> Try transliteration</Button></div>
    {r.err && <div className="mt-3"><Alert kind="error">{r.err}</Alert></div>}{out && <Result prov={out.prov}>{out.text}</Result>}</>);
}

function TranscribeDemo() {
  const { live } = useMode(); const { user } = useAuth(); const [out, setOut] = useState<string | null>(null); const [progress, setProgress] = useState<number | null>(null); const r = useRun();
  const go = (b: Blob, name: string) => r.run(async () => { setOut(null); setProgress(0); let a; try { a = await api.audioUpload(b, name, { language: "te" }, setProgress); } finally { setProgress(null); } const t = await api.transcribe({ audio_id: a.id, language: "te" }); setOut(t.transcript ?? ""); });
  if (!live) return (<><Alert kind="info" title="Demo Mode">Demo Mode has no sample audio, so nothing is transcribed. This is what a transcript looks like (ILLUSTRATIVE, hand-written):</Alert><Result prov="illustrative">{DEMO_TELUGU_TEXT}</Result></>);
  return (<NeedLogin>{user && <><p className="mb-3 text-sm">Speak a sentence in Telugu (up to 30 seconds works best; longer audio is split automatically). The recording is saved to your Audio Library.</p>
    <div className="grid gap-4 lg:grid-cols-2"><Recorder onComplete={(b, n) => void go(b, n)} label="Transcribe this recording" disabled={r.busy} /><FileDrop accept="audio/*,.wav,.mp3,.flac,.ogg,.webm,.m4a" extensions={[".wav", ".mp3", ".flac", ".ogg", ".webm", ".m4a"]} maxMB={50} title="Or upload Telugu audio" progress={progress} disabled={r.busy} onFile={(f) => void go(f, f.name)} /></div>
    {r.busy && progress === null && <p className="mt-3 text-sm" role="status">Transcribing with Bodhan…</p>}{r.err && <div className="mt-3"><Alert kind="error">{r.err}</Alert></div>}{out !== null && <Result prov="live_bodhan">{out || "(no speech was recognised)"}</Result>}</>}</NeedLogin>);
}

function OcrDemo() {
  const { live } = useMode(); const { user } = useAuth(); const [img, setImg] = useState<{ url: string; blob: Blob } | null>(null); const [out, setOut] = useState<string | null>(null); const r = useRun();
  useEffect(() => { let u: string | null = null; makeSampleLetter().then((b) => { u = URL.createObjectURL(b); setImg({ url: u, blob: b }); }).catch(() => undefined); return () => { if (u) URL.revokeObjectURL(u); }; }, []);
  const go = () => r.run(async () => {
    if (!live) { setOut(DEMO_OCR_TEXT); return; }
    const file = new File([img!.blob], "sample-letter.png", { type: "image/png" });
    const d = await api.documentUpload(file, { document_type: "letter" }); const res = await api.ocr({ document_id: d.id }); setOut(res.extracted_text ?? "");
  });
  return (<><div className="grid gap-5 md:grid-cols-2"><div><p className="label">Sample document <ProvenanceBadge value="demo" /></p>{img ? <img src={img.url} alt="Sample invented letter that begins: Dear Friend" className="w-full rounded-xl border border-line" /> : <p className="text-sm">Preparing sample…</p>}</div>
    <div><p className="text-sm">The letter is invented and drawn in your browser. In Live Mode it is saved to your Documents and read by Bodhan OCR.</p>
      <div className="mt-3"><Button onClick={() => void go()} loading={r.busy} disabled={!img || (live && !user)}><Play className="h-4 w-4" aria-hidden /> {live ? "Run OCR with Bodhan" : "Show sample output"}</Button></div>
      {live && !user && <div className="mt-3"><NeedLogin><span /></NeedLogin></div>}
      {r.err && <div className="mt-3"><Alert kind="error">{r.err}</Alert></div>}{out !== null && <Result prov={live ? "live_bodhan" : "illustrative"}>{out || "(no text found)"}</Result>}</div></div>
    <p className="mt-3 text-xs">Want to use your own? <Link className="underline" to="/digitizer">Open the Document Digitizer</Link>.</p></>);
}

function SpeakDemo() {
  const { live } = useMode(); const { user } = useAuth(); const { caps } = useCapabilities();
  const [voice, setVoice] = useState("Sravani"); const [url, setUrl] = useState<string | null>(null); const r = useRun();
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  if (!live) return <Alert kind="info" title="Demo Mode">No licensed sample audio is bundled, so nothing can be played here. In Live Mode Bodhan reads <span className="indic font-semibold">{INPUT_TELUGU}</span> aloud and you can play the real result.</Alert>;
  const voices = Object.entries(caps?.services["indic-speak"].voices ?? { Sravani: { gender: "female" }, Vamsi: { gender: "male" } });
  return (<NeedLogin>{user && <><div className="flex flex-wrap items-end gap-3"><Field label="Voice">{(id) => <Select id={id} value={voice} onChange={(e) => setVoice(e.target.value)}>{voices.map(([k, v]) => <option key={k} value={k}>{k} ({v.gender})</option>)}</Select>}</Field>
    <Button loading={r.busy} onClick={() => void r.run(async () => { const b = await api.speakPreview({ text: INPUT_TELUGU, language: "te", voice }); setUrl(URL.createObjectURL(b)); })}><Play className="h-4 w-4" aria-hidden /> Generate speech</Button></div>
    {r.err && <div className="mt-3"><Alert kind="error">{r.err}</Alert></div>}{url && <div className="mt-4"><div className="mb-1"><ProvenanceBadge value="live_bodhan" /></div><AudioPlayer url={url} title={`${voice} reading the Telugu greeting`} autoLoad /></div>}</>}</NeedLogin>);
}

export default function Try() {
  const { mode } = useMode();
  return (
    <>
      <PageHeader eyebrow="Try HeritageLens" title="See how every feature works">
        <p>Each example explains what it does, then lets you try it. In <strong>Demo Mode</strong> you see clearly labelled sample output and nothing is sent to Bodhan. In <strong>Live Mode</strong> the server makes real Bodhan calls and shows real results or real errors. Currently: <strong>{mode === "demo" ? "DEMO" : "LIVE"}</strong>. Change it in the header.</p>
      </PageHeader>
      <Container className="space-y-8">
        <Example id="story" title="1. Preserve a grandmother's Telugu story" what="Walks a spoken memory through recording, transcription, correction, translation, narration and publishing." why="Elders can contribute in their own language, and the original voice is always kept." input="A recording (or typed text), plus a title and place." how="Open the wizard and follow six steps. Your work is saved after the first step." expected="A story page with the original audio, the corrected Telugu text, translations and photographs as separate, connected versions.">
          <ol className="list-decimal space-y-1 pl-5 text-sm"><li>Record or upload the Telugu audio (original preserved).</li><li>Transcribe with indic-transcribe and correct mistakes.</li><li>Translate with indic-translate; edit the result.</li><li>Transliterate to Roman letters.</li><li>Generate narration with indic-speak.</li><li>Attach photographs, set privacy and consent, publish.</li></ol>
          <div className="mt-4 flex flex-wrap gap-3"><Link to="/preserve" className="btn-primary">Open the wizard</Link><Link to="/stories/demo-bathukamma" className="btn-secondary">See a DEMO story</Link></div></Example>
        <Example id="translate" title="2. Translation" what="Changes the language of a text." why="Researchers and younger family members can read a story they could not otherwise." input="Telugu text and a target language." how="Choose a language and press the button." expected="The text in the new language. Demo Mode shows a hand-written sample for English and Hindi only."><TranslateDemo /></Example>
        <Example id="transliterate" title="3. Transliteration" what="Writes the same language in another script, keeping pronunciation." why="People who cannot read Telugu letters can still read the sounds." input="Telugu text and an output script." how="Pick Roman or native and press the button." expected="For నమస్కారం in Roman letters, something like “Namaskaram”. The Demo output is illustrative; Live shows what Bodhan returns."><TransliterateDemo /></Example>
        <Example id="transcribe" title="4. Transcription" what="Turns speech into text." why="Spoken memories become searchable and translatable." input="A Telugu recording." how="Record or upload audio (Live Mode, signed in)." expected="The recognised Telugu text, which you can then correct."><TranscribeDemo /></Example>
        <Example id="ocr" title="5. Document OCR" what="Reads text from a scanned letter or page." why="Old documents become searchable while the original scan is kept." input="A PNG or JPEG image, or a PDF (processed page by page)." how="Use the invented sample letter or your own in the Digitizer." expected="Extracted text with layout information, shown next to the original for correction."><OcrDemo /></Example>
        <Example id="speak" title="6. Audio narration" what="Reads text aloud in a chosen voice." why="Memories become listenable, including for people who cannot read." input="Text, a language and a voice." how="Choose a voice and press the button (Live Mode, signed in)." expected="A real WAV file you can play. It is kept separate from original recordings."><SpeakDemo /></Example>
        <Example id="explore" title="7. Explore, map and timeline" what="Search the archive, see stories on a map and along a timeline." why="Discover how cultures connect across places and time." input="Nothing: just browse." how="Open the pages below." expected="Demo Mode shows sample Bathukamma, Gond art and Japanese craft records; Live Mode shows what contributors have published."><div className="flex flex-wrap gap-3"><Link to="/explore" className="btn-secondary">Explorer</Link><Link to="/map" className="btn-secondary">Map</Link><Link to="/timeline" className="btn-secondary">Timeline</Link></div></Example>
        <Example id="capsule" title="8. Message to the Future" what="Saves a written or spoken message with an intended release date." why="A parent can leave words for a child to read years later." input="A message, an optional voice recording and a date." how="Open the page and create a capsule." expected="A capsule in your dashboard. It is saved, not automatically delivered."><div className="flex flex-wrap gap-3"><Link to="/future" className="btn-secondary">Open Message to the Future</Link></div></Example>
      </Container>
    </>
  );
}
