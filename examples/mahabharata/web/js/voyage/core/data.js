// Loads the voyage JSON + asset registry and builds the shared, indexed `V` object.
// The voyage file is REQUIRED (throws with a readable message); the registry is optional.
// Every optional field is defaulted so UI code never trips over partial data.

const VOYAGE_URL = '/data/mahabharata_voyage.json';
const REGISTRY_URL = '/data/asset_registry.json';

async function fetchJSON(url, { required }) {
  let res;
  try {
    res = await fetch(url, { cache: 'no-cache' });
  } catch (err) {
    if (!required) return null;
    throw new Error(`Could not reach ${url} (${err && err.message ? err.message : 'network error'})`);
  }
  if (!res.ok) {
    if (!required) return null;
    throw new Error(`${url} answered ${res.status}${res.statusText ? ' ' + res.statusText : ''}`);
  }
  try {
    return await res.json();
  } catch (err) {
    if (!required) return null;
    throw new Error(`${url} is not valid JSON (${err.message})`);
  }
}

const arr = (v) => (Array.isArray(v) ? v : []);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

/** Lowercase, trim and also add an accent-stripped variant (IAST "Hastināpura" -> "hastinapura"). */
function nameVariants(list) {
  const out = new Set();
  for (const raw of list) {
    if (typeof raw !== 'string') continue;
    const n = raw.trim().toLowerCase();
    if (n.length < 3) continue;
    out.add(n);
    const plain = n.normalize('NFD').replace(/[̀-ͯ]/g, ''); // strip combining marks
    if (plain.length >= 3) out.add(plain);
  }
  return [...out];
}

export async function loadVoyage() {
  const [raw, reg] = await Promise.all([
    fetchJSON(VOYAGE_URL, { required: true }),
    fetchJSON(REGISTRY_URL, { required: false }),
  ]);
  if (!raw || typeof raw !== 'object') throw new Error('The voyage data file is empty.');

  const meta = {
    title: 'MAHABHARATA — The Living Epic',
    subtitle: 'The Living Epic',
    tagline: 'Every question opens another chapter. Every discovery reveals another truth.',
  };
  // Only non-empty values override the defaults (a null/"" field in partial data must not blank them).
  for (const [k, v] of Object.entries(obj(raw.meta))) if (v !== null && v !== undefined && v !== '') meta[k] = v;

  // ---- chapters (sorted by chapter_order; stable for ties / missing order)
  const chapters = arr(raw.chapters)
    .filter((c) => c && typeof c.id === 'string')
    .map((c, i) => ({
      ...c,
      narrative: arr(c.narrative),
      questions: arr(c.questions),
      related_characters: arr(c.related_characters),
      related_locations: arr(c.related_locations),
      related_events: arr(c.related_events),
      shots: arr(c.shots),
      staging: arr(c.staging),
      next_chapters: arr(c.next_chapters),
      optional_discoveries: arr(c.optional_discoveries),
      story_context: obj(c.story_context),
      navigation: obj(c.navigation),
      _i: i,
    }))
    .sort((a, b) => {
      const ao = Number.isFinite(+a.chapter_order) ? +a.chapter_order : 1e6 + a._i;
      const bo = Number.isFinite(+b.chapter_order) ? +b.chapter_order : 1e6 + b._i;
      return ao - bo || a._i - b._i;
    });
  chapters.forEach((c) => delete c._i);
  if (!chapters.length) throw new Error('The voyage data has no chapters yet.');

  const mainChapters = chapters.filter((c) => c.kind !== 'sub');
  const chapterById = new Map(chapters.map((c) => [c.id, c]));

  const locations = arr(raw.locations).filter((l) => l && l.id);
  const locationById = new Map(locations.map((l) => [l.id, l]));

  const characters = arr(raw.characters).filter((c) => c && c.id);
  const characterById = new Map(characters.map((c) => [c.id, c]));

  const relationships = arr(raw.relationships).filter((r) => r && r.from && r.to);
  const relationshipsByChar = new Map();
  for (const r of relationships) {
    for (const id of [r.from, r.to]) {
      if (!relationshipsByChar.has(id)) relationshipsByChar.set(id, []);
      relationshipsByChar.get(id).push(r);
    }
  }

  // Events: canonical list plus any chapter-authored extras (if the merge left them separate).
  const evMap = new Map();
  for (const e of [...arr(raw.events), ...arr(raw.extra_events)]) if (e && e.id && !evMap.has(e.id)) evMap.set(e.id, e);
  // Events without a numeric order sink to the end instead of jumping to the front.
  const ord = (e) => (Number.isFinite(+e.order) && e.order !== null && e.order !== '' ? +e.order : Number.MAX_SAFE_INTEGER);
  const events = [...evMap.values()].sort((a, b) => ord(a) - ord(b));
  const eventById = new Map(events.map((e) => [e.id, e]));

  // Questions: central + others, each pointing back at its chapter.
  const questionById = new Map();
  for (const ch of chapters) {
    const qs = [ch.central_question, ...ch.questions];
    for (const q of qs) if (q && q.id && !questionById.has(q.id)) questionById.set(q.id, { question: q, chapter: ch });
  }

  // Names used to spot entities inside real backend answers (case-insensitive, word-boundary matching).
  const nameIndex = [];
  for (const c of characters) {
    const names = nameVariants([c.name, ...arr(c.epithets)]);
    if (names.length) nameIndex.push({ id: c.id, type: 'character', names });
  }
  for (const l of locations) {
    const names = nameVariants([l.name, l.sanskrit_name]);
    if (names.length) nameIndex.push({ id: l.id, type: 'location', names });
  }
  for (const e of events) {
    const names = nameVariants([e.title]);
    if (names.length) nameIndex.push({ id: e.id, type: 'event', names });
  }

  return {
    meta,
    chapters,
    mainChapters,
    chapterById,
    locations,
    locationById,
    characters,
    characterById,
    relationships,
    relationshipsByChar,
    events,
    eventById,
    questionById,
    registry: obj(reg && reg.assets),
    nameIndex,
  };
}
