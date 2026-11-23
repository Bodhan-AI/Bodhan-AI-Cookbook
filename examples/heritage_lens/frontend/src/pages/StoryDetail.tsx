import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bookmark, BookmarkCheck, Calendar, Languages, MapPin, Pencil, Trash2, User } from "lucide-react";
import AudioPlayer from "../components/AudioPlayer";
import StoryCard from "../components/StoryCard";
import { StoryCover } from "../components/CoverArt";
import { Alert, Button, Container, ErrorAlert, ProvenanceBadge, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync, useBlobUrl } from "../lib/hooks";
import { fetchStories } from "../lib/archive";
import { DEMO_STORIES, DEMO_TELUGU_ENGLISH } from "../lib/demoData";
import { langName, useCapabilities } from "../lib/caps";
import { fmtDate, fmtDuration, placeOf, yearLabel } from "../lib/format";
import { categoryLabel } from "../lib/types";
import type { Bundle, Provenance, Story } from "../lib/types";

interface Version { key: string; label: string; text: string; provenance: Provenance; note?: string }

function Photo({ id, caption, attribution }: { id: number; caption: string | null; attribution: string | null }) {
  const { url, error } = useBlobUrl(api.imageFile(id));
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-white">
      {url ? <img src={url} alt={caption ?? "Photograph attached to this story"} className="h-48 w-full object-cover" loading="lazy" /> : <div className="flex h-48 items-center justify-center text-sm">{error ?? "Loading…"}</div>}
      {(caption || attribution) && <figcaption className="p-3 text-xs">{caption}{attribution && <span className="block text-mute">Credit: {attribution}</span>}</figcaption>}
    </figure>
  );
}

export default function StoryDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const { caps } = useCapabilities();
  const demo = DEMO_STORIES.find((s) => String(s.id) === id);
  const bundle = useAsync<Bundle | null>(async () => (demo ? null : api.bundle(id)), [id]);
  const savedIds = useAsync(async () => (user ? (await api.savedIds()).story_ids : []), [user?.id]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState("original");

  const story: Story | undefined = demo ?? bundle.data?.story;
  const related = useAsync(async () => (story ? (await fetchStories(demo ? "demo" : "live", { country: story.country ?? undefined, page_size: 4 })).items.filter((s) => s.id !== story.id).slice(0, 3) : []), [story?.id]);

  const versions: Version[] = useMemo(() => {
    if (!story) return [];
    const v: Version[] = [];
    if (story.original_text) v.push({ key: "original", label: `Original (${langName(story.language, caps)})`, text: story.original_text, provenance: demo ? "demo" : "user_authored" });
    if (demo && story.id === "demo-bathukamma") v.push({ key: "demo-en", label: "English (DEMO sample)", text: DEMO_TELUGU_ENGLISH, provenance: "demo", note: "Hand-written sample translation. Not produced by Bodhan." });
    bundle.data?.translations.forEach((t) => v.push({ key: `t${t.id}`, label: `${langName(t.target_language, caps)} translation`, text: t.edited_text || t.translated_text, provenance: "live_bodhan",
      note: [t.edited_text ? "Corrected by the contributor." : "Machine translation, not yet reviewed.", t.truncated ? "Warning: the translation was cut short by the service." : ""].filter(Boolean).join(" ") }));
    bundle.data?.transliterations.forEach((t) => v.push({ key: `x${t.id}`, label: `${langName(t.language, caps)} in ${t.target_script === "roman" ? "Roman" : "native"} script`, text: t.converted_text, provenance: "live_bodhan", note: "Same language, different writing system." }));
    return v;
  }, [story, bundle.data, caps, demo]);
  const current = versions.find((x) => x.key === tab) ?? versions[0];

  if (bundle.loading && !demo) return <Container className="pt-16"><Spinner label="Opening story" /></Container>;
  if (bundle.error) return <Container className="pt-16"><ErrorAlert error={bundle.error} onRetry={bundle.reload} /><Link to="/explore" className="btn-secondary mt-4">Back to Explorer</Link></Container>;
  if (!story) return null;

  const isOwner = !demo && !!bundle.data?.is_owner;
  const saved = !demo && savedIds.data?.includes(Number(story.id));
  const toggleSave = async () => { setBusy(true); setErr(null); try { saved ? await api.unsaveStory(story.id) : await api.saveStory(story.id); savedIds.reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  const remove = async () => {
    if (!window.confirm("Delete this story and its translations? Original recordings, scans and photographs are kept in your library.")) return;
    setBusy(true); try { await api.deleteStory(story.id); nav("/dashboard"); } catch (e) { setErr((e as Error).message); setBusy(false); }
  };

  return (
    <Container className="pt-8">
      <nav aria-label="Breadcrumb" className="mb-4 text-sm"><Link to="/explore" className="underline">Explorer</Link> / <span>{story.title}</span></nav>
      {demo && <div className="mb-4"><Alert kind="info" title="DEMO story">This is a sample record written for HeritageLens demonstrations. It is not real archive data.</Alert></div>}
      <div className="overflow-hidden rounded-3xl border border-line bg-white shadow-paper">
        <StoryCover imageId={story.cover_image_id} seed={typeof story.id === "number" ? story.id : 2} alt={`Cover for ${story.title}`} className="h-56 w-full sm:h-72" />
        <div className="p-6 sm:p-10">
          <div className="flex flex-wrap items-center gap-2"><span className="eyebrow">{categoryLabel(story.category)}</span><ProvenanceBadge value={story.provenance} />
            {isOwner && story.publication_status === "draft" && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-900">Draft</span>}
            {isOwner && story.privacy === "private" && <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold">Private</span>}</div>
          <h1 className="mt-2 text-3xl sm:text-5xl">{story.title}</h1>
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex gap-2"><MapPin className="h-4 w-4 shrink-0 text-clay-soft" aria-hidden /><div><dt className="sr-only">Location</dt><dd>{placeOf(story)}</dd></div></div>
            <div className="flex gap-2"><Languages className="h-4 w-4 shrink-0 text-clay-soft" aria-hidden /><div><dt className="sr-only">Original language</dt><dd>{langName(story.language, caps)}</dd></div></div>
            <div className="flex gap-2"><User className="h-4 w-4 shrink-0 text-clay-soft" aria-hidden /><div><dt className="sr-only">Contributor</dt><dd>{demo ? "HeritageLens demo" : bundle.data?.contributor}</dd></div></div>
            <div className="flex gap-2"><Calendar className="h-4 w-4 shrink-0 text-clay-soft" aria-hidden /><div><dt className="sr-only">Date contributed</dt><dd>{demo ? "Sample record" : fmtDate(story.created_at)}</dd></div></div>
          </dl>
          <div className="mt-6 flex flex-wrap gap-2">
            {!demo && user && <Button variant="secondary" onClick={toggleSave} loading={busy}>{saved ? <BookmarkCheck className="h-4 w-4" aria-hidden /> : <Bookmark className="h-4 w-4" aria-hidden />}{saved ? "Saved to your heritage" : "Save to my heritage"}</Button>}
            {!demo && !user && <Link to="/login" state={{ from: `/stories/${id}` }} className="btn-secondary"><Bookmark className="h-4 w-4" aria-hidden /> Sign in to save</Link>}
            {isOwner && <Link to={`/preserve/${story.id}`} className="btn-secondary"><Pencil className="h-4 w-4" aria-hidden /> Edit story</Link>}
            {isOwner && <Button variant="danger" onClick={remove} loading={busy}><Trash2 className="h-4 w-4" aria-hidden /> Delete</Button>}
          </div>
          {err && <div className="mt-3"><Alert kind="error">{err}</Alert></div>}
        </div>
      </div>

      <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_320px]">
        <div className="space-y-10">
          <section aria-labelledby="ctx"><h2 id="ctx" className="text-2xl">Historical context</h2>
            <p className="mt-2 whitespace-pre-line text-clay-deep/90">{story.description || "The contributor has not added context yet."}</p>
            <p className="mt-2 text-sm"><strong>Period:</strong> {story.historical_period || "Not given"} · <strong>Year:</strong> {yearLabel(story.event_year, story.event_year_approximate)}{demo && story.event_year ? " (ILLUSTRATIVE)" : ""}</p></section>

          <section aria-labelledby="text"><h2 id="text" className="text-2xl">The story</h2>
            {versions.length === 0 ? <p className="mt-2 text-sm">No written text has been added to this story yet.</p> : (
              <>
                <div role="tablist" aria-label="Story versions" className="mt-3 flex flex-wrap gap-2">
                  {versions.map((v) => <button key={v.key} role="tab" aria-selected={current?.key === v.key} onClick={() => setTab(v.key)} className={`rounded-full border px-3.5 py-1.5 text-sm font-medium ${current?.key === v.key ? "border-clay bg-clay text-white" : "border-clay-muted bg-white hover:bg-clay-pale"}`}>{v.label}</button>)}</div>
                {current && <article role="tabpanel" className="paper mt-4 p-6"><div className="mb-3 flex flex-wrap items-center gap-2"><ProvenanceBadge value={current.provenance} />{current.note && <span className="text-xs">{current.note}</span>}</div><p className="indic whitespace-pre-line text-lg">{current.text}</p></article>}
              </>)}</section>

          {(bundle.data?.audio.length ?? 0) > 0 && <section aria-labelledby="aud"><h2 id="aud" className="text-2xl">Listen</h2><div className="mt-3 space-y-3">
            {bundle.data!.audio.map((a) => <div key={a.id}><div className="mb-1 flex flex-wrap items-center gap-2 text-xs"><strong>{a.kind === "original" ? "Original recording" : `Generated narration (${a.narration_voice})`}</strong><ProvenanceBadge value={a.provenance} /><span>{langName(a.language, caps)} · {fmtDuration(a.duration)}</span></div>
              <AudioPlayer src={api.audioFile(a.id)} title={a.kind === "original" ? a.original_filename ?? "Recording" : "AI narration"} knownDuration={a.duration} />
              {(a.transcript_edited || a.transcript) && <details className="mt-2 text-sm"><summary className="cursor-pointer font-semibold">Transcript {a.transcript_edited ? "(corrected)" : "(machine, unreviewed)"}</summary><p className="indic mt-2 whitespace-pre-line">{a.transcript_edited || a.transcript}</p></details>}</div>)}</div></section>}

          {(bundle.data?.images.length ?? 0) > 0 && <section aria-labelledby="ph"><h2 id="ph" className="text-2xl">Photographs</h2><div className="mt-3 grid gap-4 sm:grid-cols-2">{bundle.data!.images.map((i) => <Photo key={i.id} id={i.id} caption={i.caption} attribution={i.attribution} />)}</div></section>}
          {isOwner && (bundle.data?.documents.length ?? 0) > 0 && <section aria-labelledby="docs"><h2 id="docs" className="text-2xl">Documents (visible only to you)</h2><ul className="mt-3 space-y-2">{bundle.data!.documents.map((d) => <li key={d.id} className="card p-3 text-sm"><Link className="font-semibold underline" to="/digitizer">{d.original_filename}</Link> · OCR: {d.ocr_status.replace("_", " ")}</li>)}</ul></section>}
        </div>

        <aside className="space-y-6">
          <section className="card p-5" aria-labelledby="tl"><h2 id="tl" className="text-lg">Story timeline</h2>
            <ol className="mt-3 space-y-3 border-l-2 border-clay-muted pl-4 text-sm">
              <li><strong>{yearLabel(story.event_year, story.event_year_approximate)}</strong><br />{story.historical_period || "Period not given"}</li>
              {!demo && <li><strong>{fmtDate(story.created_at)}</strong><br />Preserved on HeritageLens</li>}</ol></section>
          <section className="card p-5" aria-labelledby="attr"><h2 id="attr" className="text-lg">Attribution</h2>
            <p className="mt-2 text-sm">{demo ? "Sample record created by HeritageLens." : <>Shared by <strong>{bundle.data?.contributor}</strong>{story.consent_at && <> with consent confirmed on {fmtDate(story.consent_at)}</>}.</>}</p>
            {story.tags.length > 0 && <p className="mt-2 flex flex-wrap gap-1.5">{story.tags.map((t) => <span key={t} className="rounded-full bg-sand px-2 py-0.5 text-xs">{t}</span>)}</p>}</section>
        </aside>
      </div>

      {(related.data?.length ?? 0) > 0 && <section className="mt-14" aria-labelledby="rel"><h2 id="rel" className="text-2xl">Related stories</h2><div className="mt-4 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{related.data!.map((s) => <StoryCard key={s.id} story={s} />)}</div></section>}
    </Container>
  );
}
