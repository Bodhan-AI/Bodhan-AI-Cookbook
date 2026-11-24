import { Link } from "react-router-dom";
import { ArrowRight, AudioLines, BookOpenText, Globe2, Languages, ScanText, Sparkles, Users } from "lucide-react";
import Reveal from "../components/Reveal";
import StoryCard from "../components/StoryCard";
import { PatternArt } from "../components/CoverArt";
import { EmptyState, ErrorAlert, Spinner } from "../components/ui";
import { useMode } from "../lib/mode";
import { useAsync } from "../lib/hooks";
import { fetchFacets, fetchStories } from "../lib/archive";
import { langName } from "../lib/caps";

const STEPS = [["Discover", "Browse stories from any community."], ["Preserve", "Record or write a memory in your own language."], ["Digitize", "Turn old documents into searchable text."],
  ["Understand", "Read context, places and dates."], ["Translate", "Share across languages and scripts."], ["Share", "Choose who can see each story."], ["Remember", "Leave messages for the future."]];
const CAPS = [
  { icon: AudioLines, name: "indic-transcribe", text: "Turns a spoken recording into text you can correct.", to: "/audio" },
  { icon: Languages, name: "indic-translate", text: "Translates a story into other supported languages.", to: "/preserve" },
  { icon: Globe2, name: "indic-transliterate", text: "Writes the same language in Roman or native script.", to: "/scripts" },
  { icon: ScanText, name: "indic-ocr", text: "Reads letters and manuscripts from images and PDFs.", to: "/digitizer" },
  { icon: BookOpenText, name: "indic-speak", text: "Reads a story aloud in a chosen voice.", to: "/audio" },
];

export default function Home() {
  const { mode } = useMode();
  const stories = useAsync(() => fetchStories(mode, { page_size: 6, sort: "newest" }), [mode]);
  const facets = useAsync(() => fetchFacets(mode), [mode]);
  const items = stories.data?.items ?? [];
  return (
    <>
      <section className="relative overflow-hidden border-b border-line">
        <PatternArt className="absolute inset-0 h-full w-full opacity-40" seed={1} />
        <div className="relative mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
          <Reveal>
            <p className="eyebrow">A Living Memory of Humanity</p>
            <h1 className="mt-3 max-w-3xl text-4xl leading-tight sm:text-6xl">Preserve Yesterday. Inspire Tomorrow.</h1>
            <p className="mt-5 max-w-2xl text-lg text-clay-deep/90">Every community carries stories worth remembering. HeritageLens gives those stories a permanent place in humanity's digital memory.</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/explore" className="btn-primary">Explore Heritage <ArrowRight className="h-4 w-4" aria-hidden /></Link>
              <Link to="/preserve" className="btn-secondary">Preserve a Story</Link>
            </div>
          </Reveal>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-10 px-4 py-16 sm:px-6 md:grid-cols-2">
        <Reveal><p className="eyebrow">Our vision</p><h2 className="mt-2 text-3xl">Preserve the original. Make it understandable.</h2>
          <p className="mt-4 text-clay-deep/85">A grandmother in Telangana can record her childhood memories in Telugu. HeritageLens keeps her original voice, helps transcribe it, lets her correct the text, and offers translations. Each version is stored separately, so the original is never overwritten.</p></Reveal>
        <Reveal delay={0.1}><p className="eyebrow">Why it matters</p><h2 className="mt-2 text-3xl">Languages and memories fade quietly.</h2>
          <p className="mt-4 text-clay-deep/85">When an elder's stories are not written down, they are lost with the next generation. Voices, handwriting and local words are fragile. Preserving them takes ordinary people and simple tools, not specialist training.</p></Reveal>
      </section>

      <section className="bg-sand/50 py-16">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="flex items-end justify-between gap-4"><div><p className="eyebrow">{mode === "demo" ? "Sample records" : "Featured stories"}</p><h2 className="mt-1 text-3xl">Recently preserved memories</h2></div><Link to="/explore" className="btn-ghost">See all <ArrowRight className="h-4 w-4" aria-hidden /></Link></div>
          {mode === "demo" && <p className="mt-2 text-sm">These are DEMO sample records. Switch to Live to see stories shared by real contributors.</p>}
          <div className="mt-8">
            {stories.loading && <Spinner label="Loading stories" />}
            <ErrorAlert error={stories.error} onRetry={stories.reload} />
            {!stories.loading && !stories.error && items.length === 0 && (
              <EmptyState icon={<Users className="h-8 w-8" />} title="No public stories yet" action={<Link to="/preserve" className="btn-primary">Preserve the first story</Link>}>The public archive is empty. Be the first to share a memory, or switch to Demo Mode to see how stories look.</EmptyState>)}
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{items.map((s) => <StoryCard key={s.id} story={s} />)}</div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <p className="eyebrow">Explore cultures</p><h2 className="mt-1 text-3xl">Where the stories come from</h2>
        {facets.data && facets.data.countries.length > 0
          ? <ul className="mt-6 flex flex-wrap gap-3">{facets.data.countries.map((c) => <li key={c.value}><Link className="btn-secondary" to={`/explore?country=${encodeURIComponent(c.value)}`}>{c.value} <span className="text-mute">({c.count})</span></Link></li>)}
            {facets.data.languages.map((l) => <li key={l.value}><Link className="btn-secondary" to={`/explore?language=${l.value}`}>{langName(l.value)} <span className="text-mute">({l.count})</span></Link></li>)}</ul>
          : <p className="mt-4 text-sm">Countries and languages will appear here once public stories exist.</p>}
      </section>

      <section className="bg-clay-deep py-16 text-ivory">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <p className="eyebrow !text-clay-muted">How HeritageLens works</p><h2 className="mt-1 text-3xl !text-ivory">Discover → Preserve → Digitize → Understand → Translate → Share → Remember</h2>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{STEPS.map(([t, d], i) => <li key={t} className="rounded-2xl border border-clay-soft/50 p-5"><span className="font-serif text-3xl text-clay-muted">{i + 1}</span><h3 className="mt-1 text-lg !text-ivory">{t}</h3><p className="mt-1 text-sm text-clay-pale">{d}</p></li>)}</ol>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <p className="eyebrow">Five Bodhan AI capabilities</p><h2 className="mt-1 text-3xl">Language technology for Indian languages</h2>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{CAPS.map(({ icon: I, name, text, to }) => (
          <Link key={name} to={to} className="card p-5 transition hover:-translate-y-0.5"><I className="h-6 w-6 text-clay" aria-hidden /><h3 className="mt-3 text-lg">{name}</h3><p className="mt-1 text-sm text-clay-deep/80">{text}</p></Link>))}</div>
      </section>

      <section className="paper mx-auto mb-16 max-w-6xl p-8 sm:p-12">
        <div className="grid items-center gap-6 md:grid-cols-[1fr_auto]">
          <div><p className="eyebrow">Community contributions</p><h2 className="mt-1 text-3xl">Message to the Future</h2>
            <p className="mt-3 max-w-2xl text-clay-deep/85">Write or record a message for someone who may read it years from now. Capsules are saved with an intended release date. HeritageLens does not yet deliver them automatically.</p>
            {facets.data && <p className="mt-3 text-sm">{mode === "demo" ? "DEMO archive" : "Public archive"}: <strong>{facets.data.total}</strong> {facets.data.total === 1 ? "story" : "stories"}.</p>}</div>
          <Link to="/future" className="btn-primary"><Sparkles className="h-4 w-4" aria-hidden /> Write a message</Link>
        </div>
      </section>
    </>
  );
}
