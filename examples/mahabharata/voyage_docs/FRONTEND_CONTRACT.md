# Mahabharata Voyage — Frontend Contract

The voyage is an ADDITIVE frontend. The original UI (`web/index.html`, `web/js/{app,catalog,graph,background,renderers}.js`,
`web/css/style.css`) stays untouched and is served at `/classic`. `server.py` serves `web/voyage.html` at `/`.
Protected (never edit): `llm.py`, `graph_rag.py`, `keypool.py`. Also do not edit the classic files listed above.
Data shapes: `voyage_docs/DATA_CONTRACT.md`. Data files (being authored in parallel; code against the contract and
fail gracefully if a field is missing): `web/data/mahabharata_voyage.json`, `web/data/asset_registry.json`.

## Stack
- No bundler, no npm. Plain ES modules under `web/js/voyage/`, served as static files by `server.py`.
- Three.js r170 via import map (pinned):
  ```html
  <script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>
  ```
- ECharts 5.5.1 (`https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js`) and the existing classic script
  `/js/renderers.js` (exposes `window.JarvisCharts = {build(tool,args,ctx), table(tool,args), fmt, fmtFull, TOOLS, DEFAULT_TITLES, ...}`)
  are loaded as classic scripts BEFORE the module entry `/js/voyage/main.js`.
- Plotly fallback (only if an ECharts build throws): lazy-load `https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js`
  and render the server-built `figure`.
- Fonts (Google Fonts): Cormorant Garamond (400,500,600,700, italic 400/500) + DM Sans (400,500,600).

## Backend contract (unchanged endpoints)
- `GET /api/meta` → `{model, tools:[{name,description}], graph:{name, root}}`
- `POST /api/chart {query}` (Content-Type: application/json) → 200
  `{model, query, elapsed_ms, retrieval: {elapsed_ms, error, trace:[{kind:'branch'|'leaf'|'limit', path:[keys], depth, selected?:[keys], chars?}], context:[{path:"A > B", chars, content(≤700 chars)}]} | null,
    results:[{tool|null, args, error|null, text|null, figure|null}]}`
  A result with `tool:null` and `text` is the LLM's written answer. Errors: non-200 with `{error}` (502 = LLM/network).
  Calls take ~5–40 s (graph walk + LLM). Never fire duplicate concurrent requests for the same query; run at most ONE
  /api/chart at a time (Groq rate limits) and queue the rest.
- Real data only: never fabricate answers, evidence, citations, entities or chart values. Loading states mirror real
  request state. Caching a real response in memory for the session is fine (label it "from this voyage").

## Page skeleton — `web/voyage.html` (owned by the shell builder; other modules mount into these ids)
```
canvas#world-canvas            fixed full-screen WebGL canvas (z 0)
div#world-fallback             2D layered backdrop shown when WebGL unavailable (z 0)
div#transition-veil            full-screen veil for scene transitions (z 50) — prevents blank/white frames
div#hotspots                   absolutely-positioned DOM labels projected from 3D (z 10)
header#topnav                  brand, nav tabs: Journey | Epic Map | Characters | Knowledge | Timeline, progress, settings, audio, Classic link (z 40)
nav#chapter-rail               persistent chapter navigator (z 30)
section#stage-ui               current chapter panel (z 20)
aside#research                 research drawer (z 35) — expands without hiding the whole 3D scene
section#views > section#view-map, #view-characters, #view-knowledge, #view-timeline   overlay views (z 45)
div#opening                    opening sequence overlay (z 60)
div#modal-root, div#toast-root (z 70)
div#audio-controls             (inside topnav or floating)
```
`body` gets classes: `is-opening`, `view-journey|view-map|view-characters|view-knowledge|view-timeline`,
`research-open`, `research-focus` (focused research mode: research drawer wide, 3D dimmed), `reduced-motion`, `no-webgl`.

## Design tokens (define in `web/css/voyage/base.css`; everyone uses the vars, never raw hex in other CSS)
```
--night:#0B0E22; --night-2:#12152E; --night-3:#1A1D3A; --charcoal:#16161D;
--gold:#C9A24B; --gold-soft:#E3C77E; --gold-line:rgba(201,162,75,.38); --gold-faint:rgba(201,162,75,.14);
--ivory:#F3EAD7; --ivory-dim:#CFC4AE; --ivory-faint:rgba(243,234,215,.55);
--vermilion:#B5452F; --forest:#2F4A3A; --forest-light:#6E9C7E; --sandstone:#C8A57A; --celestial:#6F8FC9;
--glass:rgba(14,17,40,.66); --glass-strong:rgba(10,12,30,.86); --shadow:0 24px 60px rgba(0,0,0,.45);
--serif:'Cormorant Garamond', 'Iowan Old Style', Georgia, serif; --sans:'DM Sans', system-ui, sans-serif;
--radius:14px; --ease:cubic-bezier(.22,.61,.36,1);
```
Per-location accent: main.js sets `--loc-accent`, `--loc-sky`, `--loc-fog` on `:root` from `location.palette`.
CSS files (one per owner, all linked from voyage.html): `css/voyage/base.css` (tokens, reset, typography, buttons,
chips, glass panels, utility, focus rings), `opening.css`, `nav.css`, `chapter.css`, `views.css` (map, timeline,
characters, knowledge), `research.css`, `hotspots.css`. Mobile ≤ 760px: compact nav (tabs in a bottom bar), chapter
panel as bottom sheet, research as full-height sheet; readable 16px+ body; no horizontal scroll.
Accessibility: all controls are buttons/links with labels, visible focus (gold ring), keyboard shortcuts
(←/→ previous/next shot, M map, T timeline, C characters, K knowledge, J journey, Esc close, F fullscreen),
`prefers-reduced-motion` respected (and a manual toggle).

## Modules (ES modules; exact export names)
```
web/js/voyage/
  main.js                      boot + routing (shell builder)
  core/bus.js      export const bus = {on(evt, fn) -> unsubscribe fn, off(evt, fn), emit(evt, payload)}
  core/util.js     export {$, $$, el(html)->Element, esc, sleep, clamp, lerp, prefersReducedMotion(), fmtSecs}
  core/data.js     export async function loadVoyage() -> V      (see V below)
  core/store.js    export const store   (see Store below)
  api/backend.js   export async function askBackend(query, {signal}) -> response JSON (throws Error{status,message})
                   export async function getMeta(); export function inFlight() -> {query}|null
                   (single-flight per query, max 1 concurrent, FIFO queue, in-memory session cache keyed by query)
  world/engine.js  export async function createWorld(canvas, {V, store, bus}) -> World
  world/director.js   camera shot runner (used by engine)
  world/effects.js    sky, fog, lights, particles, mood, transitions (used by engine)
  world/assets.js     export class AssetLibrary {constructor(registry, factories); async instance(assetId, overrides) -> THREE.Object3D; status(assetId) -> 'glb'|'procedural'|'failed'}
  world/factories/index.js  export const FACTORIES  (merged from architecture.js, figures.js, objects.js, environment.js)
                   each factory: (params, ctx) -> THREE.Object3D, ctx = {THREE, materials: MaterialCache, geometries: GeometryCache, quality}
  ui/opening.js    export function runOpening({world, store, bus, audio}) -> Promise<void> (resolves after "Begin the Voyage")
  ui/nav.js        export function initNav({V, store, bus, audio, world})
  ui/chapter.js    export function initChapterPanel({V, store, bus})
  ui/hotspots.js   export function initHotspots({world, bus, V})
  ui/map.js        export function initMap({V, store, bus})
  ui/timeline.js   export function initTimeline({V, store, bus})
  ui/characters.js export function initCharacters({V, store, bus})
  ui/knowledge.js  export function initKnowledge({V, store, bus})
  research/flow.js     export function initResearch({V, store, bus, world, audio})
  research/panel.js    research drawer rendering (used by flow)
  research/evidence.js trace/context -> manuscript lineage view + entity extraction (used by panel)
  research/viz.js      export const MAHA_THEME; export function renderResult(container, result, opts) -> {dispose(), setView('chart'|'table')}
  audio/audio.js   export const audio = {unlock() (call inside a user gesture), setBed(bed, volume), cue(name, volume),
                                         setVolume(v), setMuted(bool), get muted, get volume, get unlocked}
```

### V (from loadVoyage)
```
{ meta, chapters (sorted by chapter_order), mainChapters, chapterById: Map, locations, locationById: Map,
  characters, characterById: Map, relationships, relationshipsByChar: Map<id, rel[]>, events (sorted by order),
  eventById: Map, questionById: Map<qid, {question, chapter}>, registry (asset_registry.assets object),
  nameIndex: [{id, type:'character'|'location'|'event', names:[lowercased name + epithets]}] }
```
### Store (localStorage key `mahabharata.voyage.v1`, every access in try/catch; works with storage blocked)
```
store.state = { visited:[chapterIds], answered:[questionIds], discoveries:[ids], current: chapterId|null,
                view:'journey', research: {[questionId]: {at, query, ok}},  // log only, not the answer
                settings:{quality:'auto'|'low'|'medium'|'high', reducedMotion:bool|null(=system), volume:0..1, muted:bool, ambient:bool} }
store.load(); store.save(); store.visit(id); store.answer(qid, meta); store.discover(id); store.set(path, value);
store.isUnlocked(chapter) -> bool (unlock_conditions; 'always' or conditions met); store.isVisited(id); store.isAnswered(qid)
store emits bus 'store:change' {state} after every mutation.
```
Locked chapters are SHOWN as locked (story order) but can still be opened from the map/timeline with a gentle
"you're jumping ahead" note — free exploration is never blocked. The free-form "Ask the epic" box is never locked.

### World (from createWorld) — all methods safe to call anytime; when WebGL is unavailable `supported:false` and every method is a resolved no-op
```
world.supported : bool
world.showOpening() -> Promise          cosmic sky, stars appearing, golden mandala forming, palm-leaf manuscript from darkness
world.openingZoom() -> Promise          camera glides toward the manuscript/gateway (on Begin)
world.playChapter(chapterId, {instant}) -> Promise   build/cached scene for chapter.location_id + chapter.staging, veil transition
                                        (chapter.transition), then run chapter.shots in sequence; after the last shot hold a slow idle drift
world.playShot(index) ; world.nextShot() ; world.prevShot() ; world.currentShot() -> {index, count, shot}
world.setMood('calm'|'investigate'|'reveal'|'error')   investigate: lights dim, gold motes gather; reveal: warm bloom; error: desaturate briefly
world.highlight(ids:string[])           glow staged objects whose asset id or character id is in ids ([] clears)
world.setQuality('low'|'medium'|'high'|'auto') ; world.pause() ; world.resume() ; world.dispose()
world.onHotspots(fn)                    fn([{id,label,target_type,target,x,y,visible}]) ~10 Hz with screen coords
world.stats() -> {fps, drawCalls, triangles, scenesCached}
```
Engine requirements: renderer pixel ratio capped (≤1.5 high, 1.25 medium, 1 low); ACES tone mapping, sRGB;
delta-time animation; Catmull-Rom camera splines with ease-in-out; `reducedMotion` → shots become slow cross-fades between
key frames (no fast moves, no camera shake); LRU cache of ≤3 built location scenes, dispose geometries/materials/textures
of evicted ones; shared Geometry/Material caches; InstancedMesh for scattered env (trees, soldiers, rocks);
quality auto: start medium, drop to low if fps < 30 for 3 s; pause rendering when tab hidden; never render a white
frame (renderer clear color = location sky; veil covers scene swaps). GLB loading through GLTFLoader only when the
registry entry has a non-null `glb`; on failure or null → procedural factory. Unknown asset id → small labelled
placeholder obelisk (never throw).

### Bus events (payload shapes)
```
'chapter:enter'    {chapterId, from:'nav'|'map'|'timeline'|'continue'|'discovery'|'hash'|'opening'|'characters'|'knowledge'}
'chapter:entered'  {chapterId}
'view:change'      {view:'journey'|'map'|'characters'|'knowledge'|'timeline'}
'question:select'  {questionId?, chapterId?, query?:string}   // query alone = free-form "Ask the epic"
'research:state'   {state:'focusing'|'queued'|'pending'|'retrieved'|'rendering'|'done'|'error', questionId, query, data?, error?, elapsedMs?}
'research:open' / 'research:close' / 'research:focus' {on:bool}
'world:mood'       {mood}
'world:highlight'  {ids}
'shot:change'      {chapterId, index, count, shot}
'entity:open'      {type:'character'|'location'|'event'|'chapter'|'question'|'discovery', id}
'store:change'     {state}
'settings:change'  {settings}
'audio:cue'        {cue, volume?}
'toast'            {message, kind:'info'|'error'}
```
main.js wires: `chapter:enter` → store.visit + world.playChapter + set CSS location vars + audio.setBed(chapter.ambient_audio) +
history hash `#/chapter/<id>`; hashes `#/map`, `#/timeline`, `#/characters`, `#/knowledge`, `#/chapter/<id>`;
`entity:open` → open the relevant view/card; `research:state` → world.setMood (pending→investigate, done→reveal,
error→error then calm) and world.highlight(entities found in the real response).

## Question → real pipeline flow (research/flow.js)
1 focusing: question becomes focus, drawer opens, `world:mood investigate`, highlight question.related_entities.characters.
2 queued/pending: `askBackend(question.search_query)` — the real endpoint. Elapsed timer from real start; Cancel only hides (request
  continues; result cached). Duplicate clicks on an in-flight question just focus it.
3 retrieved: replay `retrieval.trace` as an illuminated manuscript lineage (only real paths; branch → leaf), show `context`
  excerpts as discovered manuscripts labelled "Retrieved from the curated knowledge graph". If `retrieval` is null or has
  `error`, say so.
4 answer: render the `text` result (escape HTML; paragraphs; expandable).
5 charts: every result with a `tool` → `renderResult` (MAHA_THEME + semantic skin), with chart/table toggle, failed tools shown honestly.
6 discoveries: entities found by matching V.nameIndex names inside the real answer text and retrieved context (case-insensitive,
  word-boundary) → chips (character/location/event) that open cards / jump to chapters; plus the question's
  `related_entities` and `branching_options` labelled "Paths from this chapter" (from the voyage JSON).
7 done: store.answer(qid), "Continue the Voyage →" (chapter.next_chapters[0]) and branch options; reveal mood.
error: show the real error message + Retry; world mood error → calm.

## Visualization skin (research/viz.js)
`MAHA_THEME` passed as ctx to `JarvisCharts.build(tool, args, ctx)` — keys the renderer reads:
`{dark:true, colors:[...], text, sub, faint, axis, split, surface, surface2, tooltip, border, up, down, total, warn, glow, width}`.
Then a `skin(option, tool)` pass adds Mahabharata semantics WITHOUT touching data/series values/axes/labels/units:
pie/donut → mandala (thin gold rings + inner lotus graphic, padAngle, readable labels); line/area/band → golden narrative path
(smooth, gradient area, glowing line); bar/lollipop/pareto/waterfall → royal banners (rounded tops, gold edge);
sankey → river flows (gradient links); treemap/sunburst → genealogy (ivory labels, gold borders); radar → yantra grid;
heatmap/candlestick/gantt → manuscript palette; gauge → chakra dial. Fonts: serif titles, sans labels.
Table view uses `JarvisCharts.table(tool, args)`. Keep tooltips readable (ivory on night glass), legends visible,
ECharts dataZoom where the classic renderer added it.
