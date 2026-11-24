// Every record in this file is SAMPLE content written for HeritageLens demonstrations.
// It is not Bodhan output, not real archive data, and not historical fact.
import type { Facets, MapItem, Story, Capsule } from "./types";

const base = { user_id: 0, city: null, village: null, event_year_approximate: true, tags: ["demo"], privacy: "public" as const,
  publication_status: "published" as const, consent_confirmed: true, consent_at: null, created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z", provenance: "demo" as const, cover_image_id: null };

export const DEMO_TELUGU_TEXT = "బతుకమ్మ పండుగ సమయంలో మా ఊరిలో అందరూ పూలు సేకరించి కలిసి ఆడేవాళ్ళం.";
export const DEMO_TELUGU_ENGLISH = "During the Bathukamma festival, everyone in our village gathered flowers and played together.";

export const DEMO_STORIES: Story[] = [
  { ...base, id: "demo-bathukamma", title: "Memories of Bathukamma", country: "India", region: "Telangana", language: "te", category: "festival",
    description: "A demonstration story exploring memories associated with the Bathukamma festival, including flowers, community participation and family traditions.",
    historical_period: "Illustrative: living memory", event_year: 1975, event_year_approximate: true, original_text: DEMO_TELUGU_TEXT, latitude: 17.385, longitude: 78.4867 },
  { ...base, id: "demo-gond", title: "Gond Art: A Sample Record", country: "India", region: "Madhya Pradesh", language: "hi", category: "traditional_art",
    description: "A demonstration record standing in for a Gond painting tradition entry. Illustrative text only; not an art-historical source.",
    historical_period: "Illustrative: contemporary", event_year: null, original_text: null, latitude: 23.2599, longitude: 77.4126 },
  { ...base, id: "demo-japan-craft", title: "Japanese Craft: A Sample Record", country: "Japan", region: "Kyoto", language: "ja", category: "traditional_art",
    description: "A demonstration record standing in for a traditional craft entry from Japan. Illustrative text only.",
    historical_period: "Illustrative: contemporary", event_year: null, original_text: null, latitude: 35.0116, longitude: 135.7681 },
  { ...base, id: "demo-letter", title: "A Letter Home (illustrative)", country: "India", region: "Telangana", language: "te", category: "historical_document",
    description: "An invented example showing how a scanned family letter and its digitised text would be presented.",
    historical_period: "Illustrative: mid-20th century", event_year: 1962, event_year_approximate: true, original_text: null, latitude: null, longitude: null },
];

export const DEMO_MAP: MapItem[] = DEMO_STORIES.filter((s) => s.latitude != null).map((s) => ({
  id: s.id, title: s.title, description: s.description, country: s.country, region: s.region, city: null, village: null,
  language: s.language, category: s.category, latitude: s.latitude!, longitude: s.longitude!, cover_image_id: null, provenance: "demo",
  note: `DEMO pin placed at the centre of ${s.region} (${s.latitude}, ${s.longitude}); approximate, not the origin of a real memory.`,
}));

export function demoFacets(): Facets {
  const count = (pick: (s: Story) => string | null) => {
    const m = new Map<string, number>();
    DEMO_STORIES.forEach((s) => { const v = pick(s); if (v) m.set(v, (m.get(v) ?? 0) + 1); });
    return [...m].map(([value, count]) => ({ value, count }));
  };
  return { countries: count((s) => s.country), regions: count((s) => s.region), languages: count((s) => s.language),
    categories: count((s) => s.category), periods: count((s) => s.historical_period), total: DEMO_STORIES.length };
}

// ILLUSTRATIVE translations written by hand for the demo; they are NOT Bodhan output.
export const DEMO_TRANSLATIONS: Record<string, string> = {
  en: "Hello, welcome to our village.",
  hi: "नमस्कार, हमारे गाँव में आपका स्वागत है।",
};
export const DEMO_TRANSLITERATION = "Namaskaram"; // ILLUSTRATIVE
export const DEMO_OCR_TEXT = "Dear Friend,\n\nI hope this letter finds you well. The harvest festival begins next week and the whole village is preparing.\n\nYours sincerely,\nAnnapurna";

export const DEMO_CAPSULE: Capsule = {
  id: -1, title: "For Mia, on her eighteenth birthday (illustrative)", dedication: "To my daughter", provenance: "demo",
  message: "By the time you read this, the mango tree we planted will be taller than you. I hope you still hum the lullaby your grandmother taught us.",
  intended_release_date: "2036-06-01", privacy: "private", status: "sealed", has_audio: false,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  delivery_note: "ILLUSTRATIVE example. Real capsules are saved with an intended release date; HeritageLens does not yet deliver them automatically.",
};
