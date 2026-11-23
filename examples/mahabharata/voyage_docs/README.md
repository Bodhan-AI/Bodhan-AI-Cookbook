# MAHABHARATA: The Living Epic

*Every question opens another chapter. Every discovery reveals another truth.*

The voyage is a cinematic 3D front door to the JARVIS chart app. You travel the Mahabharata chapter by
chapter, from the narration at Naimisharanya to the final journey into the Himalaya. Each chapter has a
procedurally built WebGL scene with camera shots, light and sound, and it poses a few questions. When you
ask one, the voyage sends it to the **real, unchanged backend**. The backend walks a curated Mahabharata
knowledge graph (Graph-RAG), asks the LLM, and returns a written answer plus charts. The voyage then shows
the retrieval trail as an illuminated manuscript lineage, renders the charts in a Mahabharata skin, and
turns the people, places and events found in the answer into links to further chapters.

The voyage is **additive**. The original JARVIS UI is untouched and still available at `/classic`.

---

## Run it

```bash
./run.sh                 # opens http://127.0.0.1:8765
PORT=9000 ./run.sh       # another port
./run.sh --no-browser
```

| URL | What |
|---|---|
| `http://127.0.0.1:8765/` | The voyage (`web/voyage.html`). If that file is missing, the server serves the classic page instead. |
| `http://127.0.0.1:8765/classic` | The original JARVIS chart UI (`web/index.html`), unchanged. |

- **Requirements.** Python with `groq`, `python-dotenv` and `plotly` (`run.sh` installs any that are
  missing). You also need a Groq key in `.env`: `GROQ_API_KEY=...`, or several comma-separated keys in
  `GROQ_API_KEYS=...`. The optional `GROQ_MODEL` overrides the default model.
- **Knowledge graph.** The server loads `mahabharata_graph.json` by default. To switch back to the
  original demo graph, set `KNOWLEDGE_GRAPH`:
  ```bash
  KNOWLEDGE_GRAPH=helios ./run.sh        # original Helios Dynamics Group graph
  KNOWLEDGE_GRAPH=mahabharata ./run.sh   # default
  ```
  If `mahabharata_graph.json` is missing, the server logs a message and falls back to helios. `/api/meta`
  always reports the active graph as `graph: {name, root}`.
- **Browser.** The voyage needs a modern browser with ES modules and import maps. WebGL is optional: without
  it the page shows a layered 2D backdrop (`#world-fallback`, `body.no-webgl`) and every feature except the
  3D scene still works. Three.js r170 and ECharts 5.5.1 load from the jsDelivr CDN, so the first load needs network access.

---

## Architecture map

```
server.py                    static files + /api/meta, /api/chart, /api/render (adapter: KNOWLEDGE_GRAPH switch)
llm.py · graph_rag.py · keypool.py      PROTECTED, the LLM + Graph-RAG core (never edited)

web/
  voyage.html                page skeleton: canvas#world-canvas, #world-fallback, #transition-veil, #hotspots,
                             header#topnav, nav#chapter-rail, section#stage-ui, aside#research, section#views,
                             #opening, #modal-root, #toast-root, #audio-controls
  css/voyage/                base.css (design tokens) · opening · nav · chapter · views · research · hotspots
  js/voyage/
    main.js                  boot, routing (#/chapter/<id>, #/map, #/timeline, #/characters, #/knowledge), wiring
    core/  bus.js · util.js · data.js (loadVoyage → V) · store.js (localStorage progress + settings)
    api/   backend.js        askBackend(): single-flight per query, max 1 concurrent, FIFO queue, session cache
    world/ engine.js · director.js · effects.js · assets.js (AssetLibrary: GLB or procedural)
           factories/        procedural architecture, figures, objects, environment
    ui/    opening · nav · chapter · hotspots · map · timeline · characters · knowledge
    research/ flow.js (question → real pipeline) · panel.js · evidence.js (trace → manuscript lineage) · viz.js
    audio/ audio.js          procedural WebAudio sound (no audio files)
  data/
    mahabharata_voyage.json        merged voyage (chapters, locations, characters, events, relationships)
    mahabharata_voyage.schema.json
    asset_registry.json            asset slots → procedural fallback (+ future GLB path)
    asset_registry.schema.json
    voyage_parts/                  authoring parts: world.json, chapters_*.json
  index.html · js/*.js · css/style.css   CLASSIC UI, served at /classic (never edited)

data_src/graph_parts/        knowledge-graph parts g*.json
mahabharata_graph.json       merged knowledge graph (read by server.py)
tools/                       merge_voyage.py · validate_voyage.py · merge_graph.py
voyage_docs/                 FRONTEND_CONTRACT.md · DATA_CONTRACT.md · ASSET_PROMPTS.md · README.md (this file)
```

The authoritative interfaces live in two contracts:
- [`FRONTEND_CONTRACT.md`](FRONTEND_CONTRACT.md) defines module exports, DOM ids, bus events, design tokens and the backend contract.
- [`DATA_CONTRACT.md`](DATA_CONTRACT.md) defines canonical ids, coordinate conventions and every data shape.

Each module mounts into its own DOM id and talks to the others only through `core/bus.js` events and the
shared `store`.

**How a question flows** (`research/flow.js`).
1. Focus: the drawer opens and the world mood turns to "investigate".
2. Queued or pending: `askBackend(question.search_query)` posts to `POST /api/chart`. Calls take about
   5–40 s, with a real elapsed timer.
3. Retrieved: the real `retrieval.trace` and `context` are replayed as manuscript lineage.
4. Answer: the LLM's text is shown.
5. Charts: each tool result is rendered through ECharts, or through Plotly if ECharts fails.
6. Discoveries: names found in the real answer are matched against `V.nameIndex`.
7. Done: "Continue the Voyage".

Errors show the real message with a Retry button.

---

## Data pipeline

Voyage content is authored as **parts**, merged and validated. Never hand-edit the merged file.

```bash
# 1. voyage parts → merged voyage
python tools/merge_voyage.py            # web/data/voyage_parts/{world.json, chapters_*.json} → web/data/mahabharata_voyage.json
python tools/merge_voyage.py --check    # dry run, writes nothing

# 2. validate schema + referential integrity (voyage + asset registry)
python tools/validate_voyage.py                 # needs `jsonschema` (e.g. /opt/miniconda3/envs/onto/bin/python)
python tools/validate_voyage.py --graph         # also check the knowledge-graph format
python tools/validate_voyage.py --strict        # warnings fail too

# 3. knowledge-graph parts → merged graph
python tools/merge_graph.py             # data_src/graph_parts/g*.json → mahabharata_graph.json
python tools/merge_graph.py --check
```

- `merge_voyage.py` refuses to write when it finds duplicate ids. It sorts chapters by `chapter_order` and
  fills `locations[].chapters`.
- `validate_voyage.py` checks that every id resolves, that the canonical ids are all present, that assets
  exist in the registry, that `expected_topics` are real graph top-level keys, and that the shot rules hold.
- `merge_graph.py` refuses duplicate top-level keys across parts and warns about missing section-4 keys.
- Restart `./run.sh` after regenerating the graph, because the server loads it once at start.

---

## Protected-file policy

Never modify these:

| File(s) | Why |
|---|---|
| `llm.py`, `graph_rag.py`, `keypool.py` | The LLM and Graph-RAG core. `server.py` adapts it from outside by swapping `llm.GRAPH` / `llm.CONTEXT_PROMPT` at start-up. |
| `web/index.html`, `web/js/*.js` (top level), `web/css/style.css` | The classic UI at `/classic`. The voyage only *reads* `/js/renderers.js` (`window.JarvisCharts`). |
| `web/data/mahabharata_voyage.json` | A generated file. Edit the parts and re-merge. |

The backend endpoints (`/api/meta`, `/api/chart`, `/api/render`) and their response shapes are fixed.
The voyage adapts to them, never the other way round.

---

## Extending the voyage

### Add a chapter
1. Pick or create a part file in `web/data/voyage_parts/chapters_XX_YY.json` (`{"chapters": [...], "extra_events": [...]}`).
2. Add a `<Chapter>` with the following (DATA_CONTRACT §3.3):
   - a new id (`chNN`, or `chNNx` for a sub-chapter) and a `chapter_order`, which is fractional for sub-chapters;
   - a canonical `location_id`, the `narrative`, and a `story_context` that includes a `sources_note`
     wherever versions differ;
   - a `central_question` plus 3–5 `questions`;
   - `staging` and `shots`: at least 3 for main chapters and 2 for sub-chapters, with varied camera moves,
     using only registry asset ids;
   - `ambient_audio` and `transition`;
   - `unlock_conditions`, `previous_chapter` and `next_chapters`.
3. Link it from the neighbouring chapters' `next_chapters` / `previous_chapter` as needed.
4. Run `merge_voyage.py`, then `validate_voyage.py`, then reload the page.

### Add a question
Questions live inside a chapter (`central_question` or `questions[]`).
- The `id` is `q_<chapter id>_<slug>` and must be unique.
- `search_query` is the exact text sent to `/api/chart`. Name the key people and events explicitly, aim at
  the knowledge graph's top-level keys (DATA_CONTRACT §4), and ask for something chartable when it makes
  sense.
- List those keys in `expected_topics`. The validator checks them against the graph.
- Fill `related_entities`, `next_stage` and `branching_options`.

Remember that the answer will be generated live from the graph. The voyage never ships pre-written answers.

### Add or replace an asset
- **New slot.** Add the id to DATA_CONTRACT §1 and the schema's asset id list. Then add a registry entry
  with `"glb": null`, a working `fallback` (a factory from the contract's list), `bounds`, `materials` and
  `provenance`. If none of the existing factories fits, a new factory needs engine work in
  `world/factories/`.
- **Real model.** Follow [`ASSET_PROMPTS.md`](ASSET_PROMPTS.md): preview → inspect silhouette and scale →
  optimise → export GLB → validate → set the registry `glb` path → keep the procedural fallback.
  **No GLB models exist yet.** Every slot is procedural today.
- The engine loads a GLB only when `glb` is non-null, and it falls back to the factory on any failure.
  Unknown ids render as a small labelled obelisk and never throw.

---

## Sound

`web/js/voyage/audio/audio.js` synthesizes everything live with the WebAudio API. There are no audio
files and no samples.

- **Unlock.** Nothing plays until `audio.unlock()` is called inside a user gesture, such as the opening's
  "Begin the Voyage".
- **Beds.** The beds are forest, river, palace, wind, battlefield_distant, battlefield_near, celestial,
  rain, fire_crackle, assembly_murmur, mountain_wind and silence. They are built from filtered noise, slow
  LFOs and sparse scheduled events (birdsong, crackle, drums). The palace and celestial beds add a
  tanpura-like Sa–Pa drone. Changing beds is a 2.5 s equal-power crossfade.
- **Cues.** The cues are conch, war_drum, chariot_roll, bell, dice_roll, bowstring (Karplus–Strong),
  gong, wind_gust, water_flow, crowd_gasp, heartbeat, choir_drone, thunder and footsteps.
- **API.** `unlock()`, `setBed(bed, volume)` (which also accepts a chapter's `ambient_audio` object and
  schedules its `cues`), `cue(name, volume)`, `setVolume`, `setMuted`, `muted`, `volume` and `unlocked`.
  Optional extras: `setAmbient`, `setReducedMotion`, `applySettings(settings)`, `connect({store, bus})`
  (which mirrors `settings` and plays `audio:cue` events, merging duplicates), `level()` for a meter, plus
  `bed`, `ambient`, `supported`, `beds` and `cues`.
- **Wiring.** Stored settings (volume, mute, ambient, reduced motion) reach the audio engine only if
  something calls `audio.connect({store, bus})` once, or `applySettings(...)`. `main.js` does this today.
  A bed requested before `unlock()` is remembered and starts on unlock. Chapter `cues` requested before
  unlock are dropped.
- **Levels.** Beds pass through a bus compressor set to aim for about −30 dBFS, with peaks below about
  −24 dBFS. Those figures are targets taken from the compressor settings, not measurements, and nobody
  has yet checked them by ear or with a meter in a real browser. There is a master limiter. In reduced
  motion, cues are capped lower and softened, and the thunder has no crack. Muting fades out and then
  suspends the AudioContext. A hidden tab suspends it too and drops cues instead of queueing them. Without
  WebAudio every call is a no-op.

---

## Accessibility

These are the requirements every module is built to (FRONTEND_CONTRACT.md). If a module behaves
differently, that is a bug in the module, not a change of policy.

- Every control is a real button or link with a label, and focus is shown with a visible gold ring.
- Keyboard: `←`/`→` shots, `M` map, `T` timeline, `C` characters, `K` knowledge, `J` journey, `Esc` close,
  `F` fullscreen.
- `prefers-reduced-motion` is respected, and there is also a manual toggle (`store.settings.reducedMotion`:
  `null` follows the system). Under reduced motion, camera shots become slow cross-fades with no fast moves
  or shake, and audio cues are softer.
- Text stays readable: body text is 16px or larger, and charts have tooltips in ivory on night glass.
  Every chart has a table toggle (`JarvisCharts.table`).
- Mobile (≤ 760px): tabs move to a bottom bar, the chapter panel becomes a bottom sheet and research becomes
  a full-height sheet. There is no horizontal scrolling.
- Story order is shown, never enforced. Locked chapters stay openable from the map and timeline with a
  gentle note, and "Ask the epic" is always available.

## Performance

These are the engine requirements from FRONTEND_CONTRACT.md. `world.stats()` reports the live fps, draw
calls, triangles and number of cached scenes, so you can check them.

- The renderer pixel ratio is capped (≤ 1.5 high, 1.25 medium, 1 low). Quality `auto` starts at medium and
  drops to low if FPS stays below 30 for 3 s.
- At most 3 location scenes are cached (LRU). Evicted scenes have their geometry, materials and textures
  disposed. Geometry and material caches are shared, and `InstancedMesh` is used for crowds, trees and rocks.
- Rendering pauses when the tab is hidden. The veil covers scene swaps, so there are no white frames.
- Only one `/api/chart` request runs at a time (because of Groq rate limits). Later requests queue, and
  duplicate requests for an in-flight question just focus it. Responses are cached for the session and
  labelled "from this voyage".
- Audio is fully synthesized, so there are no downloads. Sparse events are scheduled ahead in AudioContext
  time, and beds stop synthesizing when ambient sound is turned off.

---

## Honesty policy

- **Curated summary, not scripture.** The knowledge graph and the chapter texts are an original, curated
  summary of the Mahabharata. They do not quote or reproduce any named edition or modern translation.
- **Recensions and traditions differ.** The epic survives in many recensions and regional traditions.
  Where episodes, numbers or motives differ, or where a story is a later or popular addition (for example
  Ganesha as scribe, Draupadi's endless sari as a miracle, the details of Karna's armour, or Barbarika),
  the text says so ("in some versions…", "the Critical Edition omits…"). Contested interpretation is never
  presented as settled fact.
- **Places "as described in the epic".** A modern identification appears only as "traditionally
  identified with …". The voyage gives no precise historical coordinates.
- **Real answers only.** Answers, evidence, entities and chart values come from the live backend
  response. Nothing is fabricated, simulated or pre-scripted. Loading states mirror the real request
  state. If retrieval is empty or errors, the UI says so.
- **Assets are honest too.** The asset registry never claims that a model file exists (`glb: null` until
  a real, validated GLB is added). Sound is labelled procedural because it is.
