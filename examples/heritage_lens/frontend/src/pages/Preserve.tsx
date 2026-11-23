import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Check } from "lucide-react";
import { ContributeStep, LanguagesStep, MaterialsStep, TranscribeStep } from "../components/PreserveSteps";
import { Alert, Button, Container, ErrorAlert, Field, PageHeader, Select, Spinner, TextArea, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { languageOptions, useCapabilities } from "../lib/caps";
import { CATEGORIES, categoryLabel } from "../lib/types";
import type { Story } from "../lib/types";
import { placeOf } from "../lib/format";

const STEPS = ["Story details", "Record or write", "Transcribe", "Translate & narrate", "Photos & documents", "Review & publish"];

interface Form { title: string; description: string; country: string; region: string; city: string; village: string; language: string; category: string; historical_period: string; event_year: string; event_year_approximate: boolean; tags: string; latitude: string; longitude: string }
const EMPTY: Form = { title: "", description: "", country: "", region: "", city: "", village: "", language: "te", category: "", historical_period: "", event_year: "", event_year_approximate: true, tags: "", latitude: "", longitude: "" };

export function validateInfo(f: Form): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.title.trim()) e.title = "Please give your story a title.";
  if (f.event_year && (!/^-?\d{1,4}$/.test(f.event_year) || Number(f.event_year) < -3000 || Number(f.event_year) > 2100)) e.event_year = "Enter a year between -3000 and 2100, or leave blank.";
  const hasLat = f.latitude.trim() !== "", hasLon = f.longitude.trim() !== "";
  if (hasLat !== hasLon) e.latitude = "Give both latitude and longitude, or leave both empty.";
  else if (hasLat && (isNaN(+f.latitude) || +f.latitude < -90 || +f.latitude > 90 || isNaN(+f.longitude) || +f.longitude < -180 || +f.longitude > 180)) e.latitude = "Latitude must be -90 to 90 and longitude -180 to 180.";
  return e;
}
const toPayload = (f: Form): Partial<Story> => ({
  title: f.title.trim(), description: f.description.trim() || null, country: f.country.trim() || null, region: f.region.trim() || null, city: f.city.trim() || null,
  village: f.village.trim() || null, language: f.language || null, category: f.category || null, historical_period: f.historical_period.trim() || null,
  event_year: f.event_year ? Number(f.event_year) : null, event_year_approximate: f.event_year_approximate,
  tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean), latitude: f.latitude ? Number(f.latitude) : null, longitude: f.longitude ? Number(f.longitude) : null,
});

export default function Preserve() {
  const { id } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const { caps } = useCapabilities();
  const [story, setStory] = useState<Story | null>(null);
  const [loading, setLoading] = useState(!!id);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [step, setStep] = useState(0);
  const [f, setF] = useState<Form>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [privacy, setPrivacy] = useState<"private" | "public">("private"); const [attribution, setAttribution] = useState(user?.display_name || user?.username || ""); const [consent, setConsent] = useState(false);
  const [done, setDone] = useState<"draft" | "published" | null>(null);

  const adopt = (s: Story) => { setStory(s); setPrivacy(s.privacy); setAttribution(s.attribution ?? attribution); setConsent(s.consent_confirmed);
    setF({ title: s.title, description: s.description ?? "", country: s.country ?? "", region: s.region ?? "", city: s.city ?? "", village: s.village ?? "", language: s.language ?? "te", category: s.category ?? "", historical_period: s.historical_period ?? "", event_year: s.event_year?.toString() ?? "", event_year_approximate: s.event_year_approximate, tags: s.tags.join(", "), latitude: s.latitude?.toString() ?? "", longitude: s.longitude?.toString() ?? "" }); };
  useEffect(() => { if (!id) return; setLoading(true); api.story(id).then(adopt).catch(setLoadError).finally(() => setLoading(false)); }, [id]); // eslint-disable-line

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  const saveInfo = async (e: FormEvent) => {
    e.preventDefault(); const found = validateInfo(f); setErrors(found); setErr(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try { const s = story ? await api.updateStory(story.id, toPayload(f)) : await api.createStory(toPayload(f)); setStory(s); if (!story) nav(`/preserve/${s.id}`, { replace: true }); setStep(1); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  const finish = async (publish: boolean) => {
    if (!story) return;
    if (publish && !consent) { setErr("Please confirm consent before publishing."); return; }
    setBusy(true); setErr(null);
    try {
      const s = await api.updateStory(story.id, { privacy, attribution: attribution.trim() || null, consent_confirmed: consent, publication_status: publish ? "published" : "draft" });
      setStory(s); setDone(publish ? "published" : "draft");
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };

  if (loading) return <Container className="pt-16"><Spinner label="Opening your draft" /></Container>;
  if (loadError) return <Container className="pt-16"><ErrorAlert error={loadError} /><Link className="btn-secondary mt-4" to="/dashboard">Back to dashboard</Link></Container>;

  if (done && story) return (
    <Container className="pt-16"><div className="paper mx-auto max-w-xl p-10 text-center"><Check className="mx-auto h-10 w-10 text-emerald-700" aria-hidden />
      <h1 className="mt-3 text-3xl">{done === "published" ? "Your story is preserved" : "Draft saved"}</h1>
      <p className="mt-3 text-sm">{done === "published" ? (story.privacy === "public" ? "It is now visible in the public Explorer." : "It is published to your private archive. Only you can see it.") : "You can continue editing it any time from your dashboard."}</p>
      <div className="mt-6 flex flex-wrap justify-center gap-3"><Link className="btn-primary" to={`/stories/${story.id}`}>View story</Link><Link className="btn-secondary" to="/dashboard">Dashboard</Link><button className="btn-ghost" onClick={() => setDone(null)}>Keep editing</button></div></div></Container>);

  return (
    <>
      <PageHeader eyebrow="Preserve a story" title={story ? story.title : "Preserve a memory"}>Take it one step at a time. Your work is saved after the first step, and your original recording is never changed.</PageHeader>
      <Container>
        <ol className="mb-8 flex flex-wrap gap-2" aria-label="Progress">
          {STEPS.map((s, i) => <li key={s}><button disabled={i > 0 && !story} onClick={() => setStep(i)} aria-current={i === step ? "step" : undefined}
            className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm disabled:opacity-40 ${i === step ? "border-clay bg-clay text-white" : i < step ? "border-clay-muted bg-clay-pale" : "border-line bg-white"}`}>
            <span className="font-semibold">{i < step ? "✓" : i + 1}</span><span className="hidden sm:inline">{s}</span></button></li>)}
        </ol>
        <div className="card p-6 sm:p-8">
          <h2 className="mb-5 text-2xl">{STEPS[step]}</h2>
          {step === 0 && (
            <form onSubmit={saveInfo} className="grid gap-5 sm:grid-cols-2" noValidate>
              {err && <div className="sm:col-span-2"><Alert kind="error">{err}</Alert></div>}
              <div className="sm:col-span-2"><Field label="Story title" error={errors.title}>{(i, d) => <TextInput id={i} value={f.title} onChange={set("title")} maxLength={200} invalid={!!errors.title} aria-describedby={d} required />}</Field></div>
              <div className="sm:col-span-2"><Field label="Short description" hint="What is this story about, and why does it matter?">{(i) => <TextArea id={i} rows={3} value={f.description} onChange={set("description")} maxLength={5000} />}</Field></div>
              <Field label="Country">{(i) => <TextInput id={i} value={f.country} onChange={set("country")} placeholder="India" />}</Field>
              <Field label="State or region">{(i) => <TextInput id={i} value={f.region} onChange={set("region")} placeholder="Telangana" />}</Field>
              <Field label="City">{(i) => <TextInput id={i} value={f.city} onChange={set("city")} />}</Field>
              <Field label="Village">{(i) => <TextInput id={i} value={f.village} onChange={set("village")} />}</Field>
              <Field label="Language of the story">{(i) => <Select id={i} value={f.language} onChange={set("language")}>{languageOptions(caps).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
              <Field label="Category">{(i) => <Select id={i} value={f.category} onChange={set("category")}><option value="">Choose a category</option>{CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</Select>}</Field>
              <Field label="Historical period" hint="For example “1950s” or “before independence”.">{(i) => <TextInput id={i} value={f.historical_period} onChange={set("historical_period")} />}</Field>
              <Field label="Year (optional)" hint="Only if you know it. Used on the timeline." error={errors.event_year}>{(i, d) => <TextInput id={i} inputMode="numeric" value={f.event_year} onChange={set("event_year")} invalid={!!errors.event_year} aria-describedby={d} />}</Field>
              <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" className="h-4 w-4 accent-clay" checked={f.event_year_approximate} onChange={(e) => setF({ ...f, event_year_approximate: e.target.checked })} /> The year is approximate</label>
              <div className="sm:col-span-2"><Field label="Tags" hint="Separate with commas.">{(i) => <TextInput id={i} value={f.tags} onChange={set("tags")} />}</Field></div>
              <details className="sm:col-span-2"><summary className="cursor-pointer text-sm font-semibold">Place on the map (optional)</summary>
                <p className="mt-2 text-xs">Only add coordinates you know are correct. Stories without coordinates simply do not appear on the map.</p>
                <div className="mt-3 grid gap-4 sm:grid-cols-2"><Field label="Latitude" error={errors.latitude}>{(i) => <TextInput id={i} inputMode="decimal" value={f.latitude} onChange={set("latitude")} />}</Field><Field label="Longitude">{(i) => <TextInput id={i} inputMode="decimal" value={f.longitude} onChange={set("longitude")} />}</Field></div></details>
              <div className="sm:col-span-2"><Button type="submit" loading={busy}>{story ? "Save and continue" : "Save and start"}</Button></div>
            </form>)}
          {story && step === 1 && <ContributeStep story={story} onStory={setStory} />}
          {story && step === 2 && <TranscribeStep story={story} onStory={setStory} />}
          {story && step === 3 && <LanguagesStep story={story} onStory={setStory} />}
          {story && step === 4 && <MaterialsStep story={story} onStory={setStory} />}
          {story && step === 5 && (
            <div className="space-y-6">
              <article className="paper p-6"><p className="eyebrow">{categoryLabel(story.category)}</p><h3 className="mt-1 text-2xl">{story.title}</h3><p className="mt-1 text-sm">{placeOf(story)} · {story.language}</p>
                <p className="mt-3 text-sm">{story.description || "No description."}</p><p className="indic mt-3 line-clamp-6 whitespace-pre-line">{story.original_text || "No story text yet."}</p></article>
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label="Who can see this story?">{(i) => <Select id={i} value={privacy} onChange={(e) => setPrivacy(e.target.value as "private" | "public")}><option value="private">Only me (private)</option><option value="public">Everyone (public archive)</option></Select>}</Field>
                <Field label="Attribution" hint="How you want to be credited.">{(i) => <TextInput id={i} value={attribution} onChange={(e) => setAttribution(e.target.value)} maxLength={200} />}</Field></div>
              <label className="flex items-start gap-3 rounded-xl border border-line bg-sand/50 p-4 text-sm"><input type="checkbox" className="mt-0.5 h-5 w-5 accent-clay" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>I am the contributor, or have permission from the people and community whose story this is, and I consent to it being preserved{privacy === "public" ? " and shown publicly" : ""} with the attribution above.</span></label>
              {err && <Alert kind="error">{err}</Alert>}
              <div className="flex flex-wrap gap-3"><Button variant="secondary" loading={busy} onClick={() => void finish(false)}>Save draft</Button><Button loading={busy} disabled={!consent} onClick={() => void finish(true)}>Publish</Button></div>
              {!consent && <p className="text-xs">Publishing needs your explicit consent.</p>}
            </div>)}
          {step > 0 && step < 5 && <div className="mt-8 flex justify-between"><Button variant="secondary" onClick={() => setStep(step - 1)}>Back</Button><Button onClick={() => setStep(step + 1)}>{step === 4 ? "Review" : "Next"}</Button></div>}
          {step === 5 && <div className="mt-6"><Button variant="ghost" onClick={() => setStep(4)}>Back</Button></div>}
        </div>
      </Container>
    </>
  );
}
