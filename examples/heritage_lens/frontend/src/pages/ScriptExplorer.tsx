import { useState } from "react";
import { Link } from "react-router-dom";
import { Copy, Eraser, Languages } from "lucide-react";
import { Alert, Button, Container, ErrorAlert, Field, LiveGate, PageHeader, ProvenanceBadge, Select, Spinner, TextArea } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useMode } from "../lib/mode";
import { useAsync } from "../lib/hooks";
import { useCapabilities } from "../lib/caps";
import { DEMO_TRANSLITERATION } from "../lib/demoData";
import { fmtDate } from "../lib/format";
import type { TransliterateResult } from "../lib/types";

export default function ScriptExplorer() {
  const { live } = useMode();
  const { user } = useAuth();
  const { caps, error: capsErr } = useCapabilities();
  const langs = caps?.services["indic-transliterate"].languages ?? {};
  const [text, setText] = useState(""); const [lang, setLang] = useState("te"); const [script, setScript] = useState("roman");
  const [result, setResult] = useState<TransliterateResult | null>(null); const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [copied, setCopied] = useState(false);
  const history = useAsync(async () => (live && user ? (await api.transliterations()).items : []), [live, user?.id]);

  const convert = async () => {
    setBusy(true); setErr(null); setResult(null);
    try { const r = await api.transliterate({ text, language: lang, script, save: true }); setResult(r); setSrc(text); history.reload(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const copy = async () => { if (!result) return; await navigator.clipboard.writeText(result.converted_text); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  return (
    <>
      <PageHeader eyebrow="Regional Script Explorer" title="Same language, different writing system">
        <p><strong>Translation</strong> changes the language. <strong>Transliteration</strong> keeps the language and its approximate pronunciation but changes the script, for example Telugu letters written in Roman letters.</p>
      </PageHeader>
      <Container className="space-y-8">
        <ErrorAlert error={capsErr} />
        <section className="card grid gap-5 p-6 md:grid-cols-[1fr_auto_auto] md:items-end">
          <div className="md:col-span-3"><Field label="Text to convert">{(id) => <TextArea id={id} rows={5} value={text} onChange={(e) => setText(e.target.value)} maxLength={20000} placeholder="నమస్కారం" />}</Field>
            <button type="button" className="mt-1 text-sm font-semibold underline" onClick={() => { setText("నమస్కారం"); setLang("te"); setScript("roman"); }}>Use the example input: నమస్కారం</button></div>
          <Field label="Language of the text" hint="English is not supported: it is already in Latin letters.">{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value)}>{Object.entries(langs).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
          <Field label="Output script">{(id) => <Select id={id} value={script} onChange={(e) => setScript(e.target.value)}><option value="roman">Roman letters</option><option value="native">Native script</option></Select>}</Field>
          <div className="flex gap-2"><Button onClick={() => void convert()} loading={busy} disabled={!live || !user || !text.trim()}><Languages className="h-4 w-4" aria-hidden /> Convert</Button><Button variant="secondary" onClick={() => { setText(""); setResult(null); setErr(null); }}><Eraser className="h-4 w-4" aria-hidden /> Clear</Button></div>
        </section>
        {!live && <Alert kind="info" title="Demo Mode: conversion is switched off">Nothing is sent to Bodhan in Demo Mode. <span className="indic">ILLUSTRATIVE example: నమస్కారం → {DEMO_TRANSLITERATION}</span> (hand-written, not a live result). Switch to Live to convert your own text.</Alert>}
        {live && !user && <Alert kind="info" title="Sign in to convert">Live conversions run through your account. <Link to="/login" state={{ from: "/scripts" }} className="font-semibold underline">Sign in</Link></Alert>}
        <LiveGate what="Transliteration"><span /></LiveGate>
        {err && <Alert kind="error" title="Conversion failed">{err}</Alert>}
        {busy && <Spinner label="Converting" />}
        {result && (
          <section aria-label="Result" className="grid gap-4 md:grid-cols-2">
            <div><h2 className="label">Original</h2><p className="indic card min-h-[8rem] whitespace-pre-wrap p-4 text-lg">{src}</p></div>
            <div><div className="flex items-center justify-between"><h2 className="label">Converted ({result.script === "roman" ? "Roman" : "native"}) <ProvenanceBadge value={result.provenance} /></h2><button onClick={() => void copy()} className="mb-1.5 inline-flex items-center gap-1 text-sm font-semibold underline"><Copy className="h-3.5 w-3.5" aria-hidden />{copied ? "Copied" : "Copy"}</button></div>
              <p className="indic card min-h-[8rem] whitespace-pre-wrap p-4 text-lg">{result.converted_text}</p>
              {result.truncated && <div className="mt-2"><Alert kind="warn">The service cut this output short. Try converting a shorter passage.</Alert></div>}</div>
          </section>)}
        {live && user && (
          <section aria-labelledby="hist"><h2 id="hist" className="text-xl">Your conversion history</h2>
            {history.data?.length === 0 && <p className="mt-2 text-sm">Nothing saved yet. Each conversion you run is saved here.</p>}
            <ul className="mt-3 grid gap-3 md:grid-cols-2">{history.data?.map((h) => <li key={h.id} className="card p-4 text-sm"><p className="text-xs text-mute">{fmtDate(h.created_at)} · {langs[h.language] ?? h.language} → {h.target_script}</p><p className="indic mt-1">{h.source_text}</p><p className="indic mt-1 font-semibold">{h.converted_text}</p>
              <button className="mt-2 text-xs font-semibold text-red-800 underline" onClick={() => void api.deleteTransliteration(h.id).then(history.reload)}>Delete</button></li>)}</ul></section>)}
      </Container>
    </>
  );
}
