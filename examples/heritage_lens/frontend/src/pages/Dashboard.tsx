import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FileText, Hourglass, Mic, PenLine, Plus, Bookmark } from "lucide-react";
import StoryCard from "../components/StoryCard";
import { Container, EmptyState, ErrorAlert, PageHeader, ProvenanceBadge, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/hooks";
import { categoryLabel } from "../lib/types";
import { fmtDate, fmtDuration } from "../lib/format";
import { langName } from "../lib/caps";

const TABS = ["stories", "drafts", "documents", "audio", "translations", "transliterations", "saved", "capsules"] as const;
type Tab = (typeof TABS)[number];
const COLORS = ["#985541", "#D5A18B"];

function Stat({ label, value, to }: { label: string; value: string | number; to?: string }) {
  const inner = <><p className="font-serif text-3xl">{value}</p><p className="text-xs uppercase tracking-wide text-mute">{label}</p></>;
  return to ? <Link to={to} className="card block p-4 hover:bg-sand/50">{inner}</Link> : <div className="card p-4">{inner}</div>;
}

function TabBody({ tab }: { tab: Tab }) {
  const data = useAsync<any>(async () => {
    switch (tab) {
      case "stories": return (await api.stories({ scope: "mine", page_size: 50 })).items;
      case "drafts": return (await api.stories({ scope: "mine", status: "draft", page_size: 50 })).items;
      case "documents": return (await api.documents()).items;
      case "audio": return (await api.audioList({ page_size: 50 })).items;
      case "translations": return (await api.translations()).items;
      case "transliterations": return (await api.transliterations()).items;
      case "saved": return (await api.saved()).items;
      case "capsules": return (await api.capsules()).items;
    }
  }, [tab]);
  const items: any[] = data.data ?? [];
  const empty: Record<Tab, [string, string, string]> = {
    stories: ["No stories yet", "Preserve your first memory.", "/preserve"], drafts: ["No drafts", "Unfinished stories appear here.", "/preserve"],
    documents: ["No documents", "Digitise a letter or manuscript.", "/digitizer"], audio: ["No recordings", "Record your first story.", "/audio"],
    translations: ["No translations", "Translate a story from the wizard.", "/preserve"], transliterations: ["No conversions", "Try the Script Explorer.", "/scripts"],
    saved: ["Nothing saved", "Save stories you love from the Explorer.", "/explore"], capsules: ["No time capsules", "Write a message to the future.", "/future"],
  };
  if (data.loading) return <Spinner />;
  if (data.error) return <ErrorAlert error={data.error} onRetry={data.reload} />;
  if (items.length === 0) return <EmptyState title={empty[tab][0]} action={<Link to={empty[tab][2]} className="btn-primary"><Plus className="h-4 w-4" aria-hidden /> Get started</Link>}>{empty[tab][1]}</EmptyState>;
  if (tab === "stories" || tab === "drafts" || tab === "saved") return <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{items.map((s) => <div key={s.id}><StoryCard story={s} />{tab !== "saved" && <Link to={`/preserve/${s.id}`} className="mt-2 inline-block text-sm font-semibold underline">Continue editing</Link>}</div>)}</div>;
  return (
    <ul className="space-y-3">{items.map((x) => (
      <li key={x.id} className="card flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
        {tab === "documents" && <><span className="font-semibold">{x.original_filename}</span><span>{x.page_count ?? 1} page(s) · OCR {x.ocr_status.replace("_", " ")}</span><Link className="underline" to="/digitizer">Open</Link></>}
        {tab === "audio" && <><span className="font-semibold">{x.kind === "original" ? x.original_filename : `Narration · ${x.narration_voice}`}</span><ProvenanceBadge value={x.provenance} /><span>{langName(x.language)} · {fmtDuration(x.duration)}</span><Link className="underline" to="/audio">Open</Link></>}
        {tab === "translations" && <><span className="font-semibold">To {langName(x.target_language)}</span><ProvenanceBadge value={x.provenance} /><span className="indic min-w-0 flex-1 truncate">{x.edited_text || x.translated_text}</span><Link className="underline" to={`/preserve/${x.story_id}`}>Story</Link></>}
        {tab === "transliterations" && <><span className="indic font-semibold">{x.source_text.slice(0, 40)}</span><span className="indic">→ {x.converted_text.slice(0, 40)}</span><Link className="underline" to="/scripts">Open</Link></>}
        {tab === "capsules" && <><span className="font-semibold">{x.title}</span><span>{x.status} · release {x.intended_release_date ? fmtDate(x.intended_release_date) : "not set"}</span><Link className="underline" to="/future">Open</Link></>}
      </li>))}</ul>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const d = useAsync(() => api.dashboard(), []);
  const [tab, setTab] = useState<Tab>("stories");
  const s = d.data;
  return (
    <>
      <PageHeader eyebrow="Your preservation workspace" title={`Welcome, ${user?.display_name || user?.username}`}>Everything here is read from your own archive. Nothing is estimated.</PageHeader>
      <Container className="space-y-10">
        {d.loading && <Spinner label="Loading your archive" />}<ErrorAlert error={d.error} onRetry={d.reload} />
        {s && (<>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">
            <Stat label="Stories" value={s.stories.total} /><Stat label="Drafts" value={s.stories.drafts} /><Stat label="Published" value={s.stories.published} /><Stat label="Documents" value={s.documents.total} to="/digitizer" />
            <Stat label="Recordings" value={s.audio.original} to="/audio" /><Stat label="Minutes recorded" value={(s.audio.original_seconds / 60).toFixed(1)} /><Stat label="Narrations" value={s.audio.generated} /><Stat label="Translations" value={s.translations} />
            <Stat label="Script conversions" value={s.transliterations} /><Stat label="Saved heritage" value={s.saved} /><Stat label="Time capsules" value={s.time_capsules.total} to="/future" /><Stat label="Digitised docs" value={s.documents.digitized} /></div>
          {s.stories.total === 0 && <EmptyState icon={<PenLine className="h-9 w-9" />} title="Your archive is empty" action={<Link to="/preserve" className="btn-primary">Preserve your first story</Link>}>Record a memory, write it down, or digitise an old letter. It all starts with one story.</EmptyState>}
          {s.stories.total > 0 && (
            <div className="grid gap-6 lg:grid-cols-2">
              <section className="card p-5" aria-labelledby="cc"><h2 id="cc" className="text-lg">Stories by category</h2>
                <div className="mt-3 h-64"><ResponsiveContainer width="100%" height="100%"><BarChart data={s.by_category.map((c) => ({ name: categoryLabel(c.category), count: c.count }))}><CartesianGrid strokeDasharray="3 3" stroke="#E9DDD3" /><XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} height={60} textAnchor="end" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="count" fill="#985541" radius={[6, 6, 0, 0]} /></BarChart></ResponsiveContainer></div></section>
              <section className="card p-5" aria-labelledby="st"><h2 id="st" className="text-lg">Drafts and published</h2>
                <div className="mt-3 h-64"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={[{ name: "Published", value: s.stories.published }, { name: "Drafts", value: s.stories.drafts }].filter((x) => x.value > 0)} dataKey="value" nameKey="name" outerRadius={90} label>{[0, 1].map((i) => <Cell key={i} fill={COLORS[i]} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer></div>
                <p className="text-xs">{s.stories.public} public · {s.stories.private} private</p></section></div>)}
          <section aria-labelledby="ra"><h2 id="ra" className="text-xl">Recent activity</h2>
            {s.recent_activity.length === 0 ? <p className="mt-2 text-sm">No activity yet.</p> : <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-white">{s.recent_activity.map((a, i) => <li key={`${a.type}-${a.id}-${i}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm"><span><span className="mr-2 rounded-full bg-sand px-2 py-0.5 text-xs">{a.type.replace("_", " ")}</span>{a.label}</span><time className="text-xs text-mute" dateTime={a.at}>{fmtDate(a.at)}</time></li>)}</ul>}</section>
        </>)}
        <section aria-labelledby="lib"><h2 id="lib" className="sr-only">Your library</h2>
          <div role="tablist" aria-label="Library sections" className="flex flex-wrap gap-2">{TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded-full border px-3.5 py-1.5 text-sm font-medium capitalize ${tab === t ? "border-clay bg-clay text-white" : "border-clay-muted bg-white hover:bg-clay-pale"}`}>{t}</button>)}</div>
          <div role="tabpanel" className="mt-5"><TabBody key={tab} tab={tab} /></div></section>
        <div className="flex flex-wrap gap-3"><Link to="/preserve" className="btn-primary"><PenLine className="h-4 w-4" aria-hidden /> Preserve a story</Link><Link to="/audio" className="btn-secondary"><Mic className="h-4 w-4" aria-hidden /> Audio</Link><Link to="/digitizer" className="btn-secondary"><FileText className="h-4 w-4" aria-hidden /> Documents</Link><Link to="/future" className="btn-secondary"><Hourglass className="h-4 w-4" aria-hidden /> Time capsules</Link><Link to="/explore" className="btn-secondary"><Bookmark className="h-4 w-4" aria-hidden /> Explore</Link></div>
      </Container>
    </>
  );
}
