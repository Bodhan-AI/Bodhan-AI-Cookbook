import { useState } from "react";
import { Link } from "react-router-dom";
import { Hourglass } from "lucide-react";
import StoryCard from "../components/StoryCard";
import { Alert, Container, EmptyState, ErrorAlert, Field, PageHeader, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useMode } from "../lib/mode";
import { useAsync } from "../lib/hooks";
import { DEMO_STORIES } from "../lib/demoData";
import { fetchFacets } from "../lib/archive";
import { langName, useCapabilities } from "../lib/caps";
import { yearLabel } from "../lib/format";
import type { Story } from "../lib/types";

export default function Timeline() {
  const { mode } = useMode();
  const { caps } = useCapabilities();
  const [country, setCountry] = useState(""); const [language, setLanguage] = useState("");
  const facets = useAsync(() => fetchFacets(mode), [mode]);
  const data = useAsync<{ dated: Story[]; undated: Story[] }>(async () => {
    if (mode === "live") return api.timeline({ country: country || undefined, language: language || undefined });
    const keep = DEMO_STORIES.filter((s) => (!country || s.country === country) && (!language || s.language === language));
    return { dated: keep.filter((s) => s.event_year != null).sort((a, b) => a.event_year! - b.event_year!), undated: keep.filter((s) => s.event_year == null) };
  }, [mode, country, language]);
  return (
    <>
      <PageHeader eyebrow="Heritage Timeline" title="Explore stories through time">Stories are placed using the year their contributor gave. Approximate years are marked “c.”. Stories without a date are listed separately; dates are never invented.</PageHeader>
      <Container className="space-y-8">
        {mode === "demo" && <Alert kind="info" title="ILLUSTRATIVE dates">Years on demo records are examples for explaining the feature, not historical facts.</Alert>}
        <div className="grid gap-4 sm:grid-cols-2"><Field label="Country">{(id) => <Select id={id} value={country} onChange={(e) => setCountry(e.target.value)}><option value="">All countries</option>{facets.data?.countries.map((c) => <option key={c.value} value={c.value}>{c.value}</option>)}</Select>}</Field>
          <Field label="Language">{(id) => <Select id={id} value={language} onChange={(e) => setLanguage(e.target.value)}><option value="">All languages</option>{facets.data?.languages.map((l) => <option key={l.value} value={l.value}>{langName(l.value, caps)}</option>)}</Select>}</Field></div>
        {data.loading && <Spinner label="Loading timeline" />}<ErrorAlert error={data.error} onRetry={data.reload} />
        {data.data && data.data.dated.length === 0 && data.data.undated.length === 0 && <EmptyState icon={<Hourglass className="h-8 w-8" />} title="Nothing on the timeline yet" action={<Link className="btn-primary" to="/preserve">Preserve a story</Link>}>Public stories will appear here once they are published.</EmptyState>}
        {data.data && data.data.dated.length > 0 && (
          <ol className="relative ml-3 space-y-10 border-l-2 border-clay-muted pl-8" aria-label="Dated stories">{data.data.dated.map((s) => (
            <li key={s.id} className="relative"><span className="absolute -left-[41px] top-1 h-4 w-4 rounded-full border-4 border-ivory bg-clay" aria-hidden />
              <p className="font-serif text-2xl">{yearLabel(s.event_year, s.event_year_approximate)}{s.provenance === "demo" && <span className="ml-2 text-xs font-sans font-semibold">(ILLUSTRATIVE)</span>}</p>
              <p className="mb-3 text-sm">{s.historical_period}</p><div className="max-w-md"><StoryCard story={s} /></div></li>))}</ol>)}
        {data.data && data.data.undated.length > 0 && <section aria-labelledby="un"><h2 id="un" className="text-2xl">Date unknown</h2><p className="text-sm">The contributors of these stories did not give a year.</p><div className="mt-4 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">{data.data.undated.map((s) => <StoryCard key={s.id} story={s} />)}</div></section>}
      </Container>
    </>
  );
}
