import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, SearchX } from "lucide-react";
import StoryCard from "../components/StoryCard";
import { Container, EmptyState, ErrorAlert, Field, PageHeader, Pagination, Select, Spinner, TextInput } from "../components/ui";
import { useMode } from "../lib/mode";
import { useAsync, useDebounced } from "../lib/hooks";
import { fetchFacets, fetchStories } from "../lib/archive";
import { CATEGORIES } from "../lib/types";
import { langName } from "../lib/caps";

const PAGE = 12;
export default function Explorer() {
  const { mode } = useMode();
  const [sp, setSp] = useSearchParams();
  const [text, setText] = useState(sp.get("q") ?? "");
  const q = useDebounced(text);
  const get = (k: string) => sp.get(k) ?? "";
  const page = Number(get("page") || 1);

  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setSp(n, { replace: true }); };
  useEffect(() => { if (q !== get("q")) set("q", q); }, [q]); // eslint-disable-line

  const facets = useAsync(() => fetchFacets(mode), [mode]);
  const res = useAsync(() => fetchStories(mode, { q: get("q"), country: get("country"), region: get("region"), language: get("language"),
    category: get("category"), period: get("period"), sort: get("sort") || "newest", page, page_size: PAGE }), [mode, sp.toString()]);
  const f = facets.data;
  const opts = (items?: { value: string; count: number }[], label?: (v: string) => string) => items?.map((i) => <option key={i.value} value={i.value}>{label ? label(i.value) : i.value} ({i.count})</option>);
  const anyFilter = ["q", "country", "region", "language", "category", "period"].some(get);

  return (
    <>
      <PageHeader eyebrow="Heritage Explorer" title="Discover stories from around the world">
        Only stories that contributors chose to publish publicly appear here. {mode === "demo" && <strong>Demo Mode: showing labelled sample records.</strong>}
      </PageHeader>
      <Container>
        <form role="search" onSubmit={(e) => e.preventDefault()} className="card grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2 lg:col-span-4"><Field label="Search stories">{(id) => <div className="relative"><Search className="pointer-events-none absolute left-3 top-3 h-5 w-5 text-mute" aria-hidden /><TextInput id={id} className="!pl-10" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search by title or description" type="search" /></div>}</Field></div>
          <Field label="Country">{(id) => <Select id={id} value={get("country")} onChange={(e) => set("country", e.target.value)}><option value="">All countries</option>{opts(f?.countries)}</Select>}</Field>
          <Field label="Region">{(id) => <Select id={id} value={get("region")} onChange={(e) => set("region", e.target.value)}><option value="">All regions</option>{opts(f?.regions)}</Select>}</Field>
          <Field label="Language">{(id) => <Select id={id} value={get("language")} onChange={(e) => set("language", e.target.value)}><option value="">All languages</option>{opts(f?.languages, (v) => langName(v))}</Select>}</Field>
          <Field label="Category">{(id) => <Select id={id} value={get("category")} onChange={(e) => set("category", e.target.value)}><option value="">All categories</option>{CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</Select>}</Field>
          <Field label="Historical period">{(id) => <Select id={id} value={get("period")} onChange={(e) => set("period", e.target.value)}><option value="">Any period</option>{opts(f?.periods)}</Select>}</Field>
          <Field label="Sort by">{(id) => <Select id={id} value={get("sort") || "newest"} onChange={(e) => set("sort", e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="title">Title A–Z</option><option value="year">Historical year</option></Select>}</Field>
          {anyFilter && <div className="flex items-end"><button type="button" className="btn-ghost" onClick={() => { setText(""); setSp({}, { replace: true }); }}>Clear filters</button></div>}
        </form>

        <div className="mt-8" aria-live="polite">
          {res.loading && <Spinner label="Searching" />}
          <ErrorAlert error={res.error} onRetry={res.reload} />
          {res.data && <p className="mb-4 text-sm">{res.data.total} {res.data.total === 1 ? "story" : "stories"} found</p>}
          {res.data && res.data.items.length === 0 && (anyFilter
            ? <EmptyState icon={<SearchX className="h-8 w-8" />} title="No stories match these filters" action={<button className="btn-secondary" onClick={() => { setText(""); setSp({}, { replace: true }); }}>Clear filters</button>}>Try fewer filters or a different search.</EmptyState>
            : <EmptyState title="The public archive is empty" action={<Link to="/preserve" className="btn-primary">Preserve a story</Link>}>No one has published a story yet.</EmptyState>)}
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{res.data?.items.map((s) => <StoryCard key={s.id} story={s} />)}</div>
          {res.data && <Pagination page={page} pageSize={PAGE} total={res.data.total} onChange={(p) => set("page", String(p))} />}
        </div>
      </Container>
    </>
  );
}
