# Mahabharata Voyage — Data Contract (source of truth for all voyage data)

All voyage data lives in `web/data/`. Authors write PART files in `web/data/voyage_parts/`; a merge step
combines them into `web/data/mahabharata_voyage.json`, validated by `web/data/mahabharata_voyage.schema.json`
plus `tools/validate_voyage.py` (schema + referential integrity).

General rules
- JSON only, UTF-8, 2-space indent, no comments, no trailing commas.
- IDs are lowercase snake_case ASCII. Never invent an ID outside the canonical lists below unless the
  section says you may (chapters may add sub-chapters `chNNx`, questions, shots, hotspots, events).
- Content honesty: the Mahabharata exists in many recensions and regional traditions. Where an episode,
  number or motive differs between traditions (or is a later/popular addition, e.g. the Ganesha-as-scribe
  story, Draupadi's "endless sari" as a divine miracle attributed to Krishna in some versions, Karna's
  "Kavacha-Kundala" details, Barbarika, etc.), say so in the text ("in some versions…", "the Critical
  Edition omits…", "popular retellings add…"). Never present contested interpretation as settled fact.
- Geography: describe places "as described in the epic". You may mention a traditional modern
  identification only as "traditionally identified with …". No precise historical coordinates.
- Moral tension, not trivia: questions open curiosity and ethical complexity.
- Original writing only; do not quote modern translations at length.

-------------------------------------------------------------------------------------------------------
## 1. Canonical IDs

### Locations (`location_id`)
naimisharanya, hastinapura, ganga, gurukul, varanavata, panchala, indraprastha, rajasuya_sabha,
dyuta_sabha, kamyaka_forest, dwaita_forest, virata, upaplavya, dwaraka, kurukshetra,
bhishma_battlefield, drona_battlefield, karna_battlefield, hastinapura_postwar, himalaya

### Characters (`character_id`)
krishna, arjuna, bhima, yudhishthira, nakula, sahadeva, draupadi, bhishma, drona, karna, duryodhana,
shakuni, kunti, gandhari, abhimanyu, vidura, dhritarashtra, pandu, madri, shantanu, ganga_devi,
satyavati, vyasa, vaishampayana, ugrashravas, janamejaya, ashwatthama, kripa, drupada, dhrishtadyumna,
shikhandi, amba, virata_king, uttara, uttara_kumari, shalya, sanjaya, ekalavya, jayadratha, dushasana,
ghatotkacha, balarama, subhadra, vikarna, yuyutsu, kichaka, parikshit, hidimbi, ulupi, chitrangada

### Events (`event_id`) — canonical timeline milestones (chapters reference these; chapters MAY add more
events with new ids in their own part files, each fully defined)
ev_narration_naimisharanya, ev_janamejaya_sacrifice, ev_shantanu_ganga, ev_bhishma_vow,
ev_amba_abduction, ev_niyoga_births, ev_pandu_curse, ev_pandavas_birth, ev_kauravas_birth,
ev_karna_birth, ev_drona_gurukul, ev_ekalavya_thumb, ev_tournament_karna, ev_lac_house,
ev_hidimba_bakasura, ev_draupadi_swayamvara, ev_shared_marriage, ev_khandava_burning,
ev_indraprastha_founded, ev_maya_sabha, ev_jarasandha_slain, ev_rajasuya, ev_shishupala_slain,
ev_dice_game, ev_draupadi_humiliation, ev_exile_begins, ev_kirmira_jatasura, ev_kirata_pashupata,
ev_yaksha_prashna, ev_karna_armour_gift, ev_virata_incognito, ev_kichaka_slain, ev_cattle_raid,
ev_peace_embassy, ev_krishna_karna_meeting, ev_kunti_karna_meeting, ev_armies_assemble,
ev_bhagavad_gita, ev_war_day_1, ev_bhishma_falls, ev_chakravyuha_abhimanyu, ev_jayadratha_slain,
ev_ghatotkacha_slain, ev_drona_slain, ev_karna_slain, ev_shalya_slain, ev_duryodhana_mace_duel,
ev_night_massacre, ev_stri_parva_lament, ev_yudhishthira_coronation, ev_bhishma_teachings,
ev_ashvamedha, ev_elders_forest_death, ev_yadava_destruction, ev_krishna_departs,
ev_mahaprasthana, ev_svargarohana

### Asset IDs (asset registry keys; shots/staging/assets[] may ONLY use these)
Architecture: arch.hastinapura_palace, arch.throne_room, arch.pillared_corridor, arch.sabha_hall,
arch.indraprastha_palace, arch.maya_sabha, arch.dice_hall, arch.gurukul, arch.hermitage,
arch.city_gate, arch.royal_courtyard, arch.temple, arch.battle_camp, arch.chariot_shelter,
arch.royal_balcony, arch.lac_house, arch.swayamvara_arena, arch.dwaraka_city, arch.yajna_altar,
arch.city_walls
Characters: `char.<character_id>` for EVERY character id above, plus char.sage_generic,
char.soldier_generic, char.courtier_generic, char.archer_generic, char.yaksha
Objects: obj.krishna_chariot, obj.chariot_generic, obj.horse, obj.gandiva, obj.sudarshana,
obj.panchajanya, obj.crown, obj.manuscript, obj.palm_leaf, obj.dice, obj.throne,
obj.divine_weapon, obj.banner, obj.kapidhvaja_banner, obj.shield, obj.arrows, obj.war_drum,
obj.palace_door, obj.lamp, obj.sacred_tree, obj.fish_target, obj.bed_of_arrows, obj.mace,
obj.conch_generic, obj.water_vessel, obj.funeral_pyre, obj.dog
Environment: env.mountains, env.himalaya_peaks, env.river, env.ganga_water, env.forest,
env.sacred_grove, env.road, env.city_skyline, env.palace_garden, env.battlefield_terrain,
env.army_formation, env.clouds, env.mist, env.dust, env.rain, env.fire, env.stars, env.sky_dawn,
env.sky_day, env.sky_dusk, env.sky_night, env.cosmic_mandala, env.snow, env.lake

### Visualization hint vocabulary (`visualization_hints[].kind`)
lineage_tree, relationship_map, alliance_sankey, timeline, army_comparison, mandala_share,
narrative_path, balance_scale, evidence_manuscript, epic_map, formation_diagram, heat_of_conflict
Each hint: {"kind": <vocab>, "suggested_tools": [one or more of the 24 chart tools: line, area, band,
bar, lollipop, waterfall, pareto, histogram, box, violin, scatter, bubble, heatmap, correlation, pie,
donut, treemap, sunburst, funnel, candlestick, gauge, radar, sankey, gantt], "note": "…"}
Hints are SUGGESTIONS for the UI; the real charts come from the backend.

### Ambient audio vocabulary (`ambient_audio.bed`) — synthesized procedurally, no audio files
forest, river, palace, wind, battlefield_distant, battlefield_near, celestial, rain, fire_crackle,
assembly_murmur, mountain_wind, silence
Cue vocabulary (`sound_cues[].cue`): conch, war_drum, chariot_roll, bell, dice_roll, bowstring,
gong, wind_gust, water_flow, crowd_gasp, heartbeat, choir_drone, thunder, footsteps

### Camera / transition vocabularies
camera_movement: establishing, tracking, dolly, orbit, crane, close_up, reveal, transition, static
transition_type: fade_black, fade_gold, cloud_pass, manuscript_turn, door_open, mandala_iris,
river_flow, path_glow, dust_wipe, cut
particle_effects[].type: dust, embers, petals, fireflies, mist, rain, snow, stars, gold_motes, sparks
lighting.preset: dawn, day, golden_hour, dusk, night, torchlit, storm, celestial, overcast

-------------------------------------------------------------------------------------------------------
## 2. Scene coordinate convention (shared by data authors and the 3D engine)
- Units ≈ metres; Y is up; ground plane y = 0. Each LOCATION is its own scene with origin at its heart.
- The location's base layout (architecture/environment) keeps a clear STAGE AREA: a disc of radius 14
  around the origin. Architecture centrepiece sits at roughly z ∈ [-60, -20]; terrain extends ±200.
- Chapters place characters/objects ON the stage via `staging` (positions inside the stage disc, or on
  architecture surfaces they know exist, e.g. throne dais at [0, 1.2, -24] for throne_room scenes).
- Human figures are ~1.8 tall (chariots ~3 long, horses ~2.4 long). Camera heights 1.6 (eye level) to
  120 (aerial). camera_path is a list of [x,y,z] waypoints (≥2) interpolated by a Catmull-Rom spline.
- Rotations are [x,y,z] in DEGREES. Scale is a number (uniform) or [x,y,z].

-------------------------------------------------------------------------------------------------------
## 3. File shapes

### 3.1 World part — `web/data/voyage_parts/world.json`
{
  "meta": {"title": "MAHABHARATA — The Living Epic", "version": "1.0.0", "subtitle": "...",
           "tagline": "Every question opens another chapter. Every discovery reveals another truth.",
           "content_note": "...honesty/recension note...", "start_chapter": "ch01"},
  "locations": [ {
     "id", "name", "sanskrit_name" (IAST optional), "kind": "forest|city|palace|river|assembly|battlefield|mountain|hermitage|kingdom",
     "description" (2–4 sentences, "as described in the epic"),
     "traditional_identification" (string or null, phrased "traditionally identified with …"),
     "map": {"x": 0..1000, "y": 0..700, "region": "north|ganga_plain|west|east|south|himalaya", "icon": "palace|forest|river|battlefield|assembly|mountain|hermitage|city"},
     "palette": {"sky": "#hex", "fog": "#hex", "ground": "#hex", "accent": "#hex", "light": "#hex"},
     "theme": "royal|wilderness|divine|conflict|sacred|aftermath",
     "base_layout": [ {"asset": <asset id>, "position": [x,y,z], "rotation": [x,y,z], "scale": n|[x,y,z], "instances": optional int for scattered copies, "scatter_radius": optional} ],
     "default_lighting": {"preset", "intensity": 0..2, "color": "#hex"},
     "ambient_audio": {"bed": <vocab>, "volume": 0..1},
     "chapters": [chapter ids set at merge time — leave []],
     "paths_to": [location ids connected on the epic map (narrative pathways)]
  } ],
  "characters": [ {
     "id", "name", "epithets": [..], "role": "...", "faction": "pandava|kaurava|neutral|divine|narrator",
     "summary" (2–3 sentences, honest), "first_chapter": "chNN",
     "visual_identity": {"primary": "#hex", "secondary": "#hex", "silhouette": "e.g. tall archer, bow over shoulder", "attributes": [asset ids e.g. obj.gandiva], "height": 1.6..2.1},
     "asset": "char.<id>"
  } ],
  "relationships": [ {"from": char id, "to": char id, "type": "parent|sibling|half_sibling|spouse|teacher|ally|rival|charioteer|friend|guardian|adoptive_parent",
                      "label": "short", "note": "optional nuance/uncertainty"} ],
  "events": [ {"id": <canonical event id>, "title", "summary" (1–2 sentences), "chapter": "chNN",
               "location_id", "characters": [ids], "order": int (narrative order 1..N),
               "parva": "Adi|Sabha|Vana|Virata|Udyoga|Bhishma|Drona|Karna|Shalya|Sauptika|Stri|Shanti|Anushasana|Ashvamedhika|Ashramavasika|Mausala|Mahaprasthanika|Svargarohana",
               "uncertainty": optional string} ]   // ALL canonical events must be defined here
}

### 3.2 Asset registry — `web/data/asset_registry.json`
{ "version": "1.0.0",
  "assets": { "<asset id>": {
     "display_name", "family": "architecture|character|object|environment",
     "glb": "assets/models/<file>.glb" or null (null = no model exists yet; NEVER claim a file exists),
     "bounds": {"size": [w,h,d]}, "scale": n, "orientation": {"forward": "-z", "up": "+y"},
     "materials": {"base": "#hex", "accent": "#hex", "emissive": "#hex|null", "roughness": 0..1, "metalness": 0..1},
     "animations": [ {"name": "idle_sway|breathe|rotate|flutter|flow|flicker|roll|draw_bow|...", "procedural": true} ],
     "fallback": {"factory": "<factory name>", "params": {...}},   // procedural factory the engine implements
     "provenance": {"source": "procedural", "author": "JARVIS voyage", "license": "original"},
     "load_status": "procedural",
     "instancing": true|false } } }
Factory names (the engine implements exactly these): palace, hall, corridor, gate, walls, courtyard,
temple, hermitage, camp, shelter, balcony, arena, city, altar, figure, chariot, horse, bow, chakra,
conch, crown, scroll, dice, throne, weapon, banner, shield, arrows, drum, door, lamp, tree, target,
arrow_bed, mace, vessel, pyre, dog, mountains, river, water, forest, grove, road, skyline, garden,
terrain, army, clouds, mist, dust, rain, fire, stars, sky, mandala, snow, lake
Figure params (for char.*): {"height", "robe": "#hex", "sash": "#hex", "skin": "#hex", "crown": bool,
"attribute": asset id|null, "pose": "standing|seated|archer|charioteer|mace|kneeling|lying|walking|blessing"}

### 3.3 Chapter part — `web/data/voyage_parts/chapters_XX_YY.json`
{ "chapters": [ <Chapter>, ... ], "extra_events": [ <Event objects as in world.events, new ids> ] }

<Chapter> = {
  "id": "ch01" (sub-chapters "ch05b"), "chapter_order": number (sub-chapters fractional e.g. 5.5),
  "kind": "main|sub", "title", "subtitle", "location_id",
  "narrative": 2–4 short paragraphs (array of strings), evocative but honest,
  "story_context": {"parva": "...", "when": "position in the story", "previously": "1–2 sentences", "sources_note": "recension/interpretation note or null"},
  "central_question": <Question>,
  "questions": [<Question> × 3–5 more],
  "related_characters": [char ids], "related_locations": [location ids], "related_events": [event ids],
  "staging": [ {"asset", "position", "rotation", "scale", "pose": optional, "id": "stg_..." } ],
  "shots": [<Shot> × ≥3 (main chapters), ≥2 (sub)],
  "assets": [all asset ids this chapter uses (base_layout of its location excluded)],
  "visualization_hints": [hint objects],
  "navigation": {"map_focus": location id, "timeline_anchor": event id, "breadcrumbs": ["Adi Parva", "..."]},
  "unlock_conditions": {"type": "always|chapter_visited|question_answered|any_of",
                        "chapters": [ids], "questions": [question ids]},
  "previous_chapter": chapter id or null, "next_chapters": [chapter ids (main next first, then branches)],
  "optional_discoveries": [ {"id": "disc_...", "title", "teaser", "type": "character|location|event|question|chapter", "target": id} ],
  "ambient_audio": {"bed": <vocab>, "volume": 0..1, "cues": [ {"cue": <vocab>, "at": seconds} ]},
  "transition": {"in": <transition_type>, "out": <transition_type>, "duration": seconds, "color": "#hex"}
}

<Question> = {
  "id": "q_ch03_vow" (unique, prefix q_<chapter id>_),
  "question": the evocative question shown to the user,
  "deeper_context": 1–3 sentences framing why it matters,
  "search_query": the exact text sent to the backend's /api/chart — phrase it so a graph walker
      over the knowledge-graph TOP-LEVEL KEYS (section 4) finds the right branches; name the key
      people/events explicitly and ask for something chartable when sensible (e.g. "Show Bhishma's vow and
      its consequences across the Kuru lineage as a family tree, and explain whether one vow changed
      the dynasty's destiny"),
  "expected_topics": [graph top-level keys / subtopics likely to be retrieved],
  "related_entities": {"characters": [], "locations": [], "events": []},
  "visualization_hints": [hint objects],
  "next_stage": {"type": "chapter|discovery|question", "target": id},
  "branching_options": [ {"label", "target_type": "chapter|question|discovery", "target": id} ] (may be [])
}

<Shot> = {
  "shot_id": "sh_ch03_01", "shot_name", "duration": seconds (4–14),
  "camera_movement": <vocab>,
  "camera_position": [x,y,z], "camera_target": [x,y,z], "camera_path": [[x,y,z], …≥2],
  "target_path": optional [[x,y,z], …] (for moving targets; same length or omitted),
  "fov": optional degrees (default 45),
  "environment": {"sky": asset id (env.sky_*), "fog_density": 0..0.05, "elements": [env asset ids]},
  "lighting": {"preset": <vocab>, "intensity": 0..2, "color": "#hex", "key_direction": [x,y,z]},
  "animation_cues": [ {"at": seconds, "target": staging id|asset id, "action": "appear|fade_in|fade_out|rise|turn_to_camera|glow|flutter|roll|draw_bow|walk_to|kneel|fall|pulse", "params": {}} ],
  "particle_effects": [ {"type": <vocab>, "density": 0..1, "color": "#hex", "area": [w,h,d]} ],
  "sound_cues": [ {"cue": <vocab>, "at": seconds, "volume": 0..1} ],
  "transition_type": <vocab> (how this shot hands to the next),
  "interactive_hotspots": [ {"id": "hs_...", "label", "position": [x,y,z], "target_type": "character|location|event|question|discovery", "target": id} ]
}
Shot cinematography must vary per chapter: establishing, tracking, dolly, orbit, crane, close-up,
reveal and transition shots — never the same generic move everywhere. No gore.

-------------------------------------------------------------------------------------------------------
## 4. Knowledge-graph top-level keys (`mahabharata_graph.json`) — search_query/expected_topics target these
"About This Knowledge Graph", "Structure of the Epic", "Frame Narratives and Narrators",
"Kuru Dynasty and Lineage", "Characters — Pandava Side", "Characters — Kaurava Side",
"Characters — Elders, Teachers and Others", "Krishna and the Yadavas",
"Youth, Education and Rivalry", "Draupadi and the Swayamvara", "Indraprastha and the Rajasuya",
"The Dice Game", "Exile in the Forest", "The Year in Virata", "Diplomacy and the Road to War",
"Armies and Alliances", "The Bhagavad Gita", "The Kurukshetra War — Day by Day",
"Battle Formations and Strategy", "Deaths of the Great Warriors", "Aftermath of the War",
"Teachings After the War", "The Final Journey", "Places of the Epic", "Concepts and Themes",
"Interpretations, Traditions and Debates"
Graph format: { top-level key: { subtopic: [fact strings] } } (two levels; leaves are lists of strings).
Numeric facts use the compact form "Label: A n | B m | …" so the chart model can read them.
