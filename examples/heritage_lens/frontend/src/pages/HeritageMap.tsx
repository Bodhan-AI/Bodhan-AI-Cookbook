import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { MapPinOff } from "lucide-react";
import MapView from "../components/MapView";
import { StoryCover } from "../components/CoverArt";
import { Alert, Container, EmptyState, ErrorAlert, Field, PageHeader, ProvenanceBadge, Select, Spinner, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { useMode } from "../lib/mode";
import { useAsync } from "../lib/hooks";
import { DEMO_MAP } from "../lib/demoData";
import { langName, useCapabilities } from "../lib/caps";
import { CATEGORIES, categoryLabel } from "../lib/types";
import { placeOf } from "../lib/format";

export default function HeritageMap() {
  const { mode } = useMode();
  const { caps } = useCapabilities();
  const [cat, setCat] = useState(""); const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | string | null>(null);
  const data = useAsync(async () => (mode === "live" ? api.locations() : { items: DEMO_MAP, total_public: DEMO_MAP.length }), [mode]);
  const items = useMemo(() => (data.data?.items ?? []).filter((i) => (!cat || i.category === cat) && (!q || `${i.title} ${i.region} ${i.country}`.toLowerCase().includes(q.toLowerCase()))), [data.data, cat, q]);
  const selected = items.find((i) => i.id === sel) ?? null;
  const onSelect = useCallback((id: number | string) => setSel(id), []);
  const hidden = (data.data?.total_public ?? 0) - (data.data?.items.length ?? 0);
  return (
    <>
      <PageHeader eyebrow="Interactive Heritage Map" title="Where memories come from">Pins appear only for public stories whose contributors supplied exact coordinates. Nothing is guessed or geocoded automatically.</PageHeader>
      <Container className="space-y-5">
        {mode === "demo" && <Alert kind="info" title="DEMO pins">These sample pins are placed at the centre of a region and are approximate. They do not mark a real memory.</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Category">{(id) => <Select id={id} value={cat} onChange={(e) => setCat(e.target.value)}><option value="">All categories</option>{CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</Select>}</Field>
          <Field label="Search">{(id) => <TextInput id={id} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, region or country" />}</Field></div>
        {data.loading && <Spinner label="Loading map data" />}<ErrorAlert error={data.error} onRetry={data.reload} />
        <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
          <MapView items={items} selectedId={sel} onSelect={onSelect} />
          <aside aria-live="polite" className="card p-4">
            {selected ? (
              <div><StoryCover imageId={selected.cover_image_id} seed={typeof selected.id === "number" ? selected.id : 1} alt={`Cover for ${selected.title}`} className="h-36 w-full rounded-xl" />
                <div className="mt-3 flex flex-wrap items-center gap-2"><span className="eyebrow">{categoryLabel(selected.category)}</span><ProvenanceBadge value={selected.provenance} /></div>
                <h2 className="mt-1 text-xl">{selected.title}</h2><p className="text-sm">{placeOf(selected)} · {langName(selected.language, caps)}</p>
                <p className="mt-2 line-clamp-5 text-sm">{selected.description}</p>{selected.note && <p className="mt-2 text-xs text-mute">{selected.note}</p>}
                <Link to={`/stories/${selected.id}`} className="btn-primary mt-4">Open story</Link></div>)
              : <p className="p-2 text-sm">Select a pin, or choose a story from the list, to preview it here.</p>}</aside></div>
        {!data.loading && items.length === 0 && <EmptyState icon={<MapPinOff className="h-8 w-8" />} title="No pins to show">{mode === "live" ? "No public story has coordinates yet. When you preserve a story, you can add its latitude and longitude." : "No sample pins match your filters."}</EmptyState>}
        {hidden > 0 && mode === "live" && <p className="text-xs">{hidden} public {hidden === 1 ? "story has" : "stories have"} no coordinates and is not shown on the map.</p>}
        <section aria-labelledby="ml"><h2 id="ml" className="text-xl">Stories on the map</h2><ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{items.map((i) => <li key={i.id}><button onClick={() => setSel(i.id)} className={`w-full rounded-xl border p-3 text-left text-sm ${sel === i.id ? "border-clay bg-clay-pale" : "border-line bg-white hover:bg-sand"}`}><strong>{i.title}</strong><br />{placeOf(i)}</button></li>)}</ul></section>
      </Container>
    </>
  );
}
