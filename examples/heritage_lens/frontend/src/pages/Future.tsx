import { useState, type FormEvent } from "react";
import { Hourglass, Lock, Pencil, Trash2 } from "lucide-react";
import AudioPlayer from "../components/AudioPlayer";
import FileDrop from "../components/FileDrop";
import Recorder from "../components/Recorder";
import { Alert, Button, Container, EmptyState, ErrorAlert, Field, PageHeader, ProvenanceBadge, Select, Spinner, TextArea, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { DEMO_CAPSULE } from "../lib/demoData";
import { fmtDate } from "../lib/format";
import type { Capsule } from "../lib/types";

export const DELIVERY_NOTE = "HeritageLens saves your message with an intended release date. It does not yet send notifications or release capsules automatically.";

function CapsuleView({ c, onEdit, onDelete }: { c: Capsule; onEdit?: () => void; onDelete?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="card overflow-hidden">
      <div className="flex items-center gap-4 bg-gradient-to-br from-clay-pale to-clay-muted p-5">
        <div className="relative h-20 w-12 shrink-0 rounded-full border-4 border-clay-deep bg-ivory" aria-hidden><div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 bg-clay-deep" /><Lock className="absolute left-1/2 top-2 h-3 w-3 -translate-x-1/2 text-clay-deep" /></div>
        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><ProvenanceBadge value={c.provenance === "demo" ? "illustrative" : c.provenance} /><span className="rounded-full bg-white/80 px-2 py-0.5 text-[11px] font-semibold uppercase">{c.status}</span><span className="rounded-full bg-white/80 px-2 py-0.5 text-[11px] font-semibold">{c.privacy}</span></div>
          <h3 className="mt-1 truncate text-xl">{c.title}</h3>{c.dedication && <p className="text-sm italic">{c.dedication}</p>}</div></div>
      <dl className="grid gap-2 p-5 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Created</dt><dd>{c.id < 0 ? "Example" : fmtDate(c.created_at)}</dd></div><div><dt className="font-semibold">Intended release</dt><dd>{c.intended_release_date ? fmtDate(c.intended_release_date) : "Not set"}</dd></div></dl>
      <div className="px-5 pb-5"><button className="text-sm font-semibold underline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide message" : "View message and details"}</button>
        {open && <div className="mt-3 space-y-3"><p className="indic whitespace-pre-wrap rounded-xl bg-sand/60 p-4">{c.message || "No written message."}</p>{c.has_audio && c.id > 0 && <AudioPlayer src={api.capsuleAudioFile(c.id)} title="Voice message" />}<p className="text-xs text-mute">{c.delivery_note}</p></div>}
        {(onEdit || onDelete) && <div className="mt-3 flex gap-2">{onEdit && <Button variant="secondary" onClick={onEdit}><Pencil className="h-4 w-4" aria-hidden /> Edit</Button>}{onDelete && <Button variant="danger" onClick={onDelete}><Trash2 className="h-4 w-4" aria-hidden /> Delete</Button>}</div>}</div>
    </article>
  );
}

export default function Future() {
  const list = useAsync(() => api.capsules(), []);
  const [editing, setEditing] = useState<Capsule | null>(null);
  const blank = { title: "", message: "", dedication: "", date: "", privacy: "private", status: "draft" };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [ok, setOk] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const edit = (c: Capsule) => { setEditing(c); setF({ title: c.title, message: c.message ?? "", dedication: c.dedication ?? "", date: c.intended_release_date ?? "", privacy: c.privacy, status: c.status }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const reset = () => { setEditing(null); setF(blank); setErrors({}); };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); const v: Record<string, string> = {};
    if (!f.title.trim()) v.title = "Give your capsule a title.";
    if (f.status === "sealed" && !f.date) v.date = "A sealed capsule needs an intended release date.";
    if (!f.message.trim() && !editing?.has_audio) v.message = "Write a message, or save as a draft and add a voice message below.";
    setErrors(v); if (Object.keys(v).length && f.status === "sealed") return; if (v.title) return;
    setBusy(true); setErr(null); setOk(null);
    const body = { title: f.title.trim(), message: f.message || null, dedication: f.dedication || null, intended_release_date: f.date || null, privacy: f.privacy as Capsule["privacy"], status: f.status as Capsule["status"] };
    try { const c = editing ? await api.updateCapsule(editing.id, body) : await api.createCapsule(body); setEditing(c); setOk(editing ? "Capsule updated." : "Capsule saved. You can now add a voice message."); list.reload(); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  const addAudio = async (b: Blob, name: string) => { if (!editing) return; setErr(null); setProgress(0); try { setEditing(await api.capsuleAudioUpload(editing.id, b, name, setProgress)); setOk("Voice message attached."); list.reload(); } catch (x) { setErr((x as Error).message); } finally { setProgress(null); } };
  return (
    <>
      <PageHeader eyebrow="Message to the Future" title="Leave words for someone who is not here yet">Write or record a message for a child, a grandchild or a community. <strong>{DELIVERY_NOTE}</strong></PageHeader>
      <Container className="space-y-10">
        <form onSubmit={submit} noValidate className="card space-y-5 p-6" aria-labelledby="cap-form"><h2 id="cap-form" className="text-2xl">{editing ? `Editing “${editing.title}”` : "New time capsule"}</h2>
          {err && <Alert kind="error">{err}</Alert>}{ok && <Alert kind="success">{ok}</Alert>}
          <div className="grid gap-5 sm:grid-cols-2"><Field label="Title" error={errors.title}>{(id, d) => <TextInput id={id} value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} invalid={!!errors.title} aria-describedby={d} />}</Field>
            <Field label="Dedication" hint="For example “To my daughter Mia”.">{(id) => <TextInput id={id} value={f.dedication} maxLength={200} onChange={(e) => setF({ ...f, dedication: e.target.value })} />}</Field></div>
          <Field label="Your message" error={errors.message}>{(id, d) => <TextArea id={id} rows={8} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} aria-describedby={d} />}</Field>
          <div className="grid gap-5 sm:grid-cols-3"><Field label="Intended release date" error={errors.date}>{(id, d) => <TextInput id={id} type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} invalid={!!errors.date} aria-describedby={d} />}</Field>
            <Field label="Privacy">{(id) => <Select id={id} value={f.privacy} onChange={(e) => setF({ ...f, privacy: e.target.value })}><option value="private">Only me</option><option value="public">Public</option></Select>}</Field>
            <Field label="Status">{(id) => <Select id={id} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="draft">Draft</option><option value="sealed">Sealed (release date set)</option></Select>}</Field></div>
          <div className="flex gap-3"><Button type="submit" loading={busy}>{editing ? "Save changes" : "Save capsule"}</Button>{editing && <Button type="button" variant="secondary" onClick={reset}>Start a new one</Button>}</div>
          {editing && <div className="border-t border-line pt-5"><h3 className="text-lg">Voice message {editing.has_audio && "(attached)"}</h3>
            <div className="mt-3 grid gap-4 lg:grid-cols-2"><Recorder onComplete={(b, n) => void addAudio(b, n)} label="Attach this recording" disabled={progress !== null} />
              <FileDrop accept="audio/*,.wav,.mp3,.flac,.ogg,.webm,.m4a" extensions={[".wav", ".mp3", ".flac", ".ogg", ".webm", ".m4a"]} maxMB={50} title="Or upload audio" progress={progress} disabled={progress !== null} onFile={(x) => void addAudio(x, x.name)} /></div></div>}
        </form>
        <section aria-labelledby="mine"><h2 id="mine" className="text-2xl">Your capsules</h2>
          {list.loading && <Spinner />}<ErrorAlert error={list.error} onRetry={list.reload} />
          {list.data?.items.length === 0 && <div className="mt-3"><EmptyState icon={<Hourglass className="h-8 w-8" />} title="No capsules yet">Write your first message above. Here is an illustrative example of how one looks:</EmptyState></div>}
          <div className="mt-4 grid gap-5 md:grid-cols-2">{list.data?.items.map((c) => <CapsuleView key={c.id} c={c} onEdit={() => edit(c)} onDelete={() => { if (window.confirm("Delete this capsule permanently?")) void api.deleteCapsule(c.id).then(() => { if (editing?.id === c.id) reset(); list.reload(); }); }} />)}
            {list.data?.items.length === 0 && <CapsuleView c={DEMO_CAPSULE} />}</div></section>
      </Container>
    </>
  );
}
