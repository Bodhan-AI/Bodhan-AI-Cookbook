import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Download, FileText, ScanText, Trash2 } from "lucide-react";
import FileDrop from "../components/FileDrop";
import { Alert, Button, Container, EmptyState, ErrorAlert, Field, LiveGate, PageHeader, ProvenanceBadge, Select, Spinner, TextArea, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { useAsync, useBlobUrl, useDebounced } from "../lib/hooks";
import { download, fmtBytes, fmtDate } from "../lib/format";
import type { DocumentRec } from "../lib/types";

export function composeText(d: DocumentRec): string {
  if (d.extracted_edited_text != null) return d.extracted_edited_text;
  const pages = (d.ocr_pages ?? []).filter((p) => !p.error);
  if (pages.length <= 1) return pages[0]?.markdown ?? d.extracted_text ?? "";
  return pages.map((p) => `— Page ${p.page_number} —\n\n${p.markdown}`).join("\n\n");
}

function Workspace({ doc, onChanged, onDeleted }: { doc: DocumentRec; onChanged: (d?: DocumentRec) => void; onDeleted: () => void }) {
  const preview = useBlobUrl(api.documentFile(doc.id));
  const [page, setPage] = useState(0);
  const [text, setText] = useState(composeText(doc));
  const [view, setView] = useState<"pages" | "edit">("pages");
  const [fmt, setFmt] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [ok, setOk] = useState<string | null>(null);
  useEffect(() => { setText(composeText(doc)); setPage(0); setOk(null); setErr(null); }, [doc.id, doc.extracted_text, doc.extracted_edited_text]);
  const pages = doc.ocr_pages ?? [];
  const run = async (fn: () => Promise<unknown>, msg?: string) => { setBusy(true); setErr(null); setOk(null); try { await fn(); if (msg) setOk(msg); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const isPdf = doc.mime_type === "application/pdf";
  const cur = pages[page];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-2xl">{doc.original_filename}</h2>
        <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={!preview.url} onClick={() => preview.url && Object.assign(document.createElement("a"), { href: preview.url, download: doc.original_filename }).click()}><Download className="h-4 w-4" aria-hidden /> Original</Button>
          <Button variant="danger" onClick={() => { if (window.confirm("Delete this document and its extracted text permanently?")) void run(async () => { await api.documentDelete(doc.id); onDeleted(); }); }}><Trash2 className="h-4 w-4" aria-hidden /> Delete</Button></div></div>
      <p className="text-sm">{fmtBytes(doc.size_bytes)} · {doc.page_count ?? 1} page(s) · uploaded {fmtDate(doc.created_at)} · OCR: <strong>{doc.ocr_status.replace("_", " ")}</strong> {doc.ocr_provenance && <ProvenanceBadge value={doc.ocr_provenance} />}</p>
      {err && <Alert kind="error">{err}</Alert>}{ok && <Alert kind="success">{ok}</Alert>}
      {doc.ocr_status === "partial" && <Alert kind="warn" title="Some pages could not be read">{doc.ocr_error}</Alert>}
      <LiveGate what="OCR">
        <div className="flex flex-wrap items-end gap-3"><Field label="Table output (optional)">{(id) => <Select id={id} value={fmt} onChange={(e) => setFmt(e.target.value)}><option value="">Service default</option><option value="html">HTML</option><option value="markdown">Markdown</option></Select>}</Field>
          <Button loading={busy} onClick={() => void run(async () => { const d = await api.ocr({ document_id: doc.id, table_format: fmt || undefined, force: doc.ocr_status !== "not_run" && doc.ocr_status !== "failed" }); onChanged(d); }, "Text extracted. Please check it against the original.")}><ScanText className="h-4 w-4" aria-hidden /> {doc.ocr_status === "not_run" || doc.ocr_status === "failed" ? "Extract text with Bodhan" : "Run OCR again"}</Button></div>
      </LiveGate>
      <div className="grid gap-5 lg:grid-cols-2">
        <section aria-label="Original document" className="min-w-0"><h3 className="mb-2 text-lg">Original</h3>
          <div className="overflow-hidden rounded-xl border border-line bg-sand/50">
            {preview.loading && <div className="p-8"><Spinner label="Loading preview" /></div>}{preview.error && <div className="p-4"><Alert kind="error">{preview.error}</Alert></div>}
            {preview.url && (isPdf ? <iframe title={`Preview of ${doc.original_filename}`} src={preview.url} className="h-[520px] w-full" /> : <img src={preview.url} alt={`Scan of ${doc.original_filename}`} className="max-h-[560px] w-full object-contain" />)}</div></section>
        <section aria-label="Extracted text" className="min-w-0"><div className="mb-2 flex items-center justify-between"><h3 className="text-lg">Extracted text</h3>
          <div role="tablist" className="flex gap-1">{(["pages", "edit"] as const).map((v) => <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)} className={`rounded-full px-3 py-1 text-sm ${view === v ? "bg-clay text-white" : "bg-white border border-line"}`}>{v === "pages" ? "By page" : "Edit"}</button>)}</div></div>
          {doc.ocr_status === "not_run" ? <EmptyState icon={<FileText className="h-7 w-7" />} title="Not digitised yet">Run OCR to extract the text. The original file stays untouched either way.</EmptyState>
            : view === "pages" ? (
              <div className="rounded-xl border border-line bg-white p-4">
                <div className="mb-3 flex items-center justify-between text-sm"><Button variant="ghost" disabled={page <= 0} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button><span aria-live="polite">Page {cur?.page_number ?? 1} of {pages.length || 1}</span><Button variant="ghost" disabled={page >= pages.length - 1} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button></div>
                {cur?.error ? <Alert kind="error" title="This page could not be read">{cur.error}</Alert> : <pre className="indic max-h-[460px] overflow-auto whitespace-pre-wrap break-words font-sans text-sm">{cur?.markdown ?? doc.extracted_text}</pre>}
                <p className="mt-2 text-xs">Machine output, shown exactly as returned. Use “Edit” to correct it.</p></div>)
              : (<div><TextArea rows={18} value={text} onChange={(e) => setText(e.target.value)} aria-label="Edit extracted text" />
                <div className="mt-2 flex flex-wrap gap-2"><Button loading={busy} onClick={() => void run(async () => { onChanged(await api.documentEditText(doc.id, text)); }, "Your corrections were saved. The raw OCR output is kept separately.")}>Save corrections</Button>
                  <Button variant="secondary" onClick={() => download(`${doc.original_filename.replace(/\.[^.]+$/, "")}.txt`, text)}><Download className="h-4 w-4" aria-hidden /> Download text</Button>
                  {doc.extracted_edited_text != null && <Button variant="ghost" onClick={() => setText(doc.extracted_text ?? "")}>Reset to OCR output</Button>}</div></div>)}</section>
      </div>
    </div>
  );
}

export default function Digitizer() {
  const [q, setQ] = useState(""); const dq = useDebounced(q);
  const docs = useAsync(() => api.documents({ q: dq || undefined }), [dq]);
  const [sel, setSel] = useState<number | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [dtype, setDtype] = useState("letter");
  const [err, setErr] = useState<string | null>(null);
  const current = useMemo(() => docs.data?.items.find((d) => d.id === sel) ?? null, [docs.data, sel]);
  const upload = async (f: File) => { setErr(null); setProgress(0); try { const d = await api.documentUpload(f, { document_type: dtype }, setProgress); docs.reload(); setSel(d.id); } catch (e) { setErr((e as Error).message); } finally { setProgress(null); } };
  return (
    <>
      <PageHeader eyebrow="Document Digitizer" title="Turn old documents into searchable text">Upload a letter, manuscript or scanned page (image or PDF). The original is stored untouched; text extracted by Bodhan is stored separately so you can compare and correct it. PDFs are processed page by page.</PageHeader>
      <Container className="grid gap-8 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-5">
          <Field label="Document type">{(id) => <Select id={id} value={dtype} onChange={(e) => setDtype(e.target.value)}>{["letter", "manuscript", "printed", "photograph", "other"].map((d) => <option key={d}>{d}</option>)}</Select>}</Field>
          <FileDrop accept="application/pdf,image/png,image/jpeg" extensions={[".pdf", ".png", ".jpg", ".jpeg"]} maxMB={25} title="Upload a document" progress={progress} disabled={progress !== null} onFile={(f) => void upload(f)} />
          {err && <Alert kind="error">{err}</Alert>}
          <Field label="Search your documents">{(id) => <TextInput id={id} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="File name or extracted text" />}</Field>
          {docs.loading && <Spinner />}<ErrorAlert error={docs.error} onRetry={docs.reload} />
          <ul className="space-y-2" aria-label="Your documents">{docs.data?.items.map((d) => <li key={d.id}><button onClick={() => setSel(d.id)} aria-current={sel === d.id} className={`w-full rounded-xl border p-3 text-left text-sm ${sel === d.id ? "border-clay bg-clay-pale" : "border-line bg-white hover:bg-sand"}`}><span className="block truncate font-semibold">{d.original_filename}</span><span className="text-xs">{d.page_count ?? 1} page(s) · OCR {d.ocr_status.replace("_", " ")}</span></button></li>)}</ul>
          {docs.data?.items.length === 0 && !docs.loading && <p className="text-sm">{dq ? "No documents match your search." : "No documents yet. Upload one above."}</p>}
        </aside>
        <div className="min-w-0">{current ? <Workspace key={current.id} doc={current} onChanged={() => docs.reload()} onDeleted={() => { setSel(null); docs.reload(); }} />
          : <EmptyState icon={<ScanText className="h-9 w-9" />} title="Select or upload a document" action={<Link to="/try" className="btn-secondary">See how it works</Link>}>Your original scan and its extracted text will appear side by side.</EmptyState>}</div>
      </Container>
    </>
  );
}
