# Mahabharata Voyage: 3D Asset Generation Prompts

> **Status: none of these assets exist yet.** Every entry in `web/data/asset_registry.json` currently has
> `"glb": null` and is drawn by a procedural factory (`fallback.factory`) in `web/js/voyage/world/factories/`.
> This document holds the **briefs and prompts** for making real GLB models later, in Blender or with a
> text-to-3D tool such as Tripo, Meshy or Rodin, followed by hand clean-up. Nothing here should be read as a
> claim that a model, texture or scan already exists.

The registry slot id (e.g. `arch.hastinapura_palace`) is the contract. A finished GLB is wired in by setting
that slot's `glb` path, and only then. The procedural fallback stays in place for good.

---

## Contents

1. [Global art direction](#1-global-art-direction)
2. [Technical conventions (all assets)](#2-technical-conventions-all-assets)
3. [Replacement workflow (preview to registry)](#3-replacement-workflow)
4. [Optimisation & export checklist](#4-optimisation--export-checklist)
5. [Architecture (`arch.*`)](#5-architecture)
6. [Characters (`char.*`)](#6-characters)
7. [Objects (`obj.*`)](#7-objects)
8. [Environment (`env.*`)](#8-environment)
9. [Slot coverage index](#9-slot-coverage-index)

---

## 1. Global art direction

**Look.** The look is *original, stylised mythological*. It sits between painted temple sculpture and
miniature painting: clean, readable silhouettes; slightly elongated, graceful proportions; and surfaces
that look like painted stone, lacquered wood, hammered gold, raw silk and burnished bronze. Detail is
concentrated at faces, hands, ornaments and architectural cornices. Large surfaces stay calm so the
cinematic lighting (ACES tone mapping, warm key, cool fog) can sculpt them.

**Palette.** Use the voyage design tokens: night `#0B0E22`, gold `#C9A24B`, soft gold `#E3C77E`, ivory
`#F3EAD7`, vermilion `#B5452F`, forest `#2F4A3A`, sandstone `#C8A57A`, celestial blue `#6F8FC9`. Each
location also has its own palette (`locations[].palette`). Models should read well under all of the
lighting presets: dawn, day, golden_hour, dusk, night, torchlit, storm, celestial and overcast.

**Originality (mandatory).**
- No likeness to any real person, living or dead. No actor's face, body or styling.
- No costume, armour, crown, set or prop design taken from any film, television serial, animated series,
  comic, stage production or video game adaptation of the Mahabharata or Ramayana.
- Classical *iconographic* attributes from the textual and devotional traditions are allowed and
  encouraged, provided the execution is original. Examples: Krishna's dark complexion, yellow silk and
  peacock feather; Gandhari's blindfold; Bhima's mace.
- Paste the shared **negative prompt** below into every text-to-3D generation:

```
NEGATIVE: photorealistic celebrity face, actor likeness, portrait of a real person, film still,
TV serial costume, cosplay, anime style, chibi, cartoon mascot, plastic toy look, modern clothing,
modern weapons, text, logos, watermark, gore, blood, wounds, nudity, sexualised pose, exaggerated
musculature, horror, low-poly blob, melted geometry, fused fingers, extra limbs
```

**Respect.** These are sacred and culturally central figures. Poses must be dignified. Show no gore and no
wounds. Death scenes are implied by stillness and light, never by injury. Women must not be sexualised.
Shakuni, Duryodhana and the other antagonists are regal and human, not caricatures, and no ethnic
caricature is permitted for any figure.

**Honesty.** Where an attribute varies between recensions or is a later popular addition (for example
Karna's armour and earrings, the details of the dice, or the number of serrations on the Sudarshana), the
design keeps it *suggestive, not literal*, and the relevant entry says so.

---

## 2. Technical conventions (all assets)

| Topic | Rule |
|---|---|
| Units | 1 unit = 1 metre. Model at real-world scale; registry `scale` stays `1` unless noted. |
| Axes | **+Y up, front faces +Z.** The front means the entrance, facade, stairs or a figure's face. A GLB must face **the same way as the procedural fallback it replaces**, because the engine applies no correcting rotation to GLBs (`world/assets.js` only applies the `base_layout`/`staging` transform). The architecture factories (`world/factories/architecture.js`: "front faces +z, towards the stage when the asset sits at negative z") put the front toward +Z, as do the interior layouts in §5, where thrones and daises sit at negative z facing the entrance. +Z is also glTF's own convention ("the front of a glTF asset faces +Z"). The glTF exporter's *+Y Up* option maps Blender (x, y, z) to glTF (x, z, −y), so in Blender (Z-up) the front faces **−Y**: you see the model's face in Front view (Numpad 1). To confirm in a three.js viewer, a camera at +Z looking toward −Z should see the asset's **front**. **Known inconsistency (engine owner to resolve before the first GLB lands):** every registry entry currently says `orientation.forward: "-z"`, and `world/director.js` uses that field to choose the direction for `walk_to` and `turn_to_camera`. Until that is reconciled, always check the GLB in the voyage next to its procedural fallback. If the two disagree, follow the fallback and report the mismatch. Do not rotate the GLB root to compensate. |
| Pivot / origin | Ground-standing assets: **bottom-centre at y = 0**. Hand-held props: origin at the **grip**, with a `ground` empty for when the prop is set down. Rotating parts (wheels, chakra, flags) get their own node pivoted at the rotation axis. |
| Coordinates in scenes | Location scenes keep a clear stage disc of radius 14 m around the origin. Architecture centrepieces are placed by `base_layout` around z ∈ [−60, −20]. Terrain extends ±200 m (DATA_CONTRACT §2). |
| Human scale | Principal figures 1.66–2.0 m (registry `visual_identity.height`); chariot ≈ 3 m long; horse ≈ 2.4 m long. |
| Materials | glTF 2.0 metal/rough PBR only: baseColor (sRGB), ORM packed (R = AO, G = roughness, B = metalness, linear), normal (linear, OpenGL +Y), and emissive only where noted. `KHR_materials_emissive_strength` is allowed. No other extensions unless the loader supports them (see §4). |
| Material count | Characters ≤ 2, props ≤ 2, architecture ≤ 6 (prefer trim sheets and atlases). |
| Textures | Power-of-two sizes. Characters 2048² (body) + 1024² (ornaments); props 1024²; architecture 2048² trim sheets plus tiling 1024² materials; environment tiling 1024². |
| Node names | `snake_case`, prefixed by role: `wheel_L`, `banner_mount`, `seat_charioteer`, `flag_cloth`, `leaf_03`. Empties mark attach points. |
| Animation | The engine animates procedurally: idle_sway, breathe, rotate, flutter, flow, flicker, roll, draw_bow. GLBs should be **static** and expose correctly pivoted nodes. Embedded clips are optional extras named exactly as the registry `animations[].name`. |
| Instancing | Assets with registry `instancing: true` (soldiers, trees, rocks, arrows, generics) must be **one mesh, one material, no skinning, no morph targets**, so the engine can use `InstancedMesh`. |
| Forbidden in GLB | Lights, cameras, scene-level transforms ≠ identity, negative scales, unapplied modifiers, n-gons in final export, duplicate vertices, non-manifold shells on characters. |

### Poly and size budgets

| Tier | Triangles (LOD0) | LOD1 | GLB target (after optimisation) |
|---|---|---|---|
| Hero architecture (palaces, halls) | ≤ 150 k | ≤ 50 k | ≤ 6 MB |
| Secondary architecture | ≤ 60 k | ≤ 20 k | ≤ 3 MB |
| Principal character | ≤ 30 k | ≤ 10 k | ≤ 3 MB |
| Secondary / generic character (instanced) | ≤ 8 k (generic ≤ 6 k) | ≤ 3 k | ≤ 1 MB |
| Hero prop (chariot + horses) | ≤ 60 k | ≤ 20 k | ≤ 4 MB |
| Hand prop (bow, conch, chakra, dice, manuscript) | ≤ 12 k | ≤ 4 k | ≤ 1 MB |
| Small / instanced prop (arrow, lamp, rock) | ≤ 2 k | ≤ 600 | ≤ 300 KB |
| Terrain / environment set | ≤ 80 k | ≤ 25 k | ≤ 4 MB |

LODs are optional. If you provide them, export them as sibling nodes named `<name>_LOD0` and `<name>_LOD1`.
The engine does not yet switch LODs, so LOD0 alone must still meet its budget.

---

## 3. Replacement workflow

Follow these steps for every asset, in order. A model that fails a step does not move on to the next one.

1. **Preview.** Generate or model a draft from the prompt. With text-to-3D, generate 4 to 8 candidates,
   pick the cleanest silhouette, and remesh or retopologise it by hand. Raw generator meshes are never final.
2. **Inspect silhouette & scale.** Import the draft into Blender next to a 1.8 m reference human and a
   1 m cube. Check the following:
   - The silhouette reads at thumbnail size, as a black fill against white.
   - Height, width and depth match the brief's *Scale* line to within ±5 %.
   - The front faces −Y in Blender (+Z in glTF; see §2 Axes), matching the procedural fallback, and the
     origin sits where the brief says.
   - There is no resemblance to any real person or adaptation (see §1). Have a second person check this.
3. **Clean & optimise.** Retopologise to budget, unwrap UVs (no overlaps except mirrored trims), bake
   normals and AO from the high-poly, pack ORM, apply transforms (Ctrl+A → All Transforms), merge
   duplicate vertices, and recalculate normals outward.
4. **Export GLB.** Use Blender File → Export → glTF 2.0 with these settings: *Format: glTF Binary*,
   *+Y Up*, *Apply Modifiers*, *Selected Objects*, no cameras or lights, *Materials: Export*,
   *Images: Automatic*, and *Compression* off (compression happens in step 5). Save the file to
   `web/assets/models/<slot_without_family_prefix>.glb`, e.g. `web/assets/models/hastinapura_palace.glb`.
5. **Optimise the file** with glTF-Transform or gltfpack (see §4).
6. **Validate.**
   - Run the Khronos glTF Validator (CLI `gltf_validator file.glb`, or drag the file onto
     https://github.khronos.org/glTF-Validator/). It must report **0 errors**. Warnings must be reviewed.
   - Run `gltf-transform inspect file.glb` and confirm triangles, textures, sizes and extensions are within budget.
   - Open the file in the three.js editor (https://threejs.org/editor/) to confirm orientation, scale
     and materials under a neutral HDRI and with ACES tone mapping.
7. **Set the registry `glb` path.** In `web/data/asset_registry.json`, for the slot only:
   - `"glb": "assets/models/hastinapura_palace.glb"` (the path is relative to `web/`; the schema pattern
     is `^assets/models/…\.glb$`).
   - Update `bounds.size` to the measured size and `provenance` to the true source, author and licence,
     e.g. `{"source": "Blender, original", "author": "<name>", "license": "CC-BY-4.0"}` or
     `{"source": "Tripo text-to-3D + manual retopology", ...}`. Never write a provenance you cannot back up.
   - `load_status`: the schema currently allows only `"procedural"`. Leave it unchanged. The engine reports
     the real runtime state through `AssetLibrary.status(id)` (`'glb'|'procedural'|'failed'`). Extend the
     schema enum only as a deliberate, reviewed change.
   - Then run `python tools/validate_voyage.py`. It must pass.
8. **Keep the procedural fallback.** Never delete or weaken `fallback`. If the GLB is missing, 404s, fails
   to parse or uses an unsupported extension, the engine falls back to the factory. Test this by temporarily
   pointing `glb` at a non-existent file and checking that the scene still renders.

---

## 4. Optimisation & export checklist

Do these steps offline. They are part of the asset pipeline, not the app: the voyage itself has no build step.

```bash
# inspect
gltf-transform inspect in.glb

# safe baseline that needs NO extra decoder in the browser (GLTFLoader reads WebP natively):
# dedup, prune, weld, cap texture size, WebP textures. No geometry compression.
gltf-transform dedup in.glb a.glb && gltf-transform prune a.glb b.glb && gltf-transform weld b.glb c.glb
gltf-transform resize c.glb d.glb --width 2048 --height 2048
gltf-transform webp d.glb out.glb            # or keep PNG/JPEG

# maximum compression (ONLY if the engine registers the decoders, see below)
gltf-transform optimize in.glb out.glb --compress meshopt --texture-compress ktx2
#   or Draco geometry:   --compress draco
#   or gltfpack:         gltfpack -i in.glb -o out.glb -cc -tc
# (KTX2 encoding needs KTX-Software's `toktx` on PATH. These CLIs are offline tools, not app dependencies.)
```

> **Decoder check.** `meshopt`, Draco and KTX2 files load only if `web/js/voyage/world/assets.js`
> registers `MeshoptDecoder`, `DRACOLoader` or `KTX2Loader` on its `GLTFLoader` (all available under
> `three/addons/`). **When this document was written it registered none of them** (a plain `GLTFLoader`),
> so use the safe baseline unless that has changed. Check with `grep -nE "Meshopt|DRACO|KTX2" web/js/voyage/world/assets.js`. If a
> decoder is missing, use the safe baseline above. If the engine cannot read a file it falls back to
> the procedural factory, and the asset silently stays procedural.

Per-asset checklist (copy it into the asset's PR or notes):

- [ ] Scale matches the brief (±5 %). Origin and pivots are as specified. +Y up, front faces +Z, the same facing as the procedural fallback (checked side by side in the voyage).
- [ ] Triangle count is within the tier budget. Material count is within its limit. Textures are power-of-two and ≤ the tier size.
- [ ] Textures are KTX2 (ETC1S for baseColor/ORM, UASTC for normals) **or** WebP if no KTX2 decoder is present.
- [ ] Geometry is compressed with meshopt or Draco **only if** the decoder is registered. Quantisation is checked visually for cracks.
- [ ] Instanced assets: a single mesh and a single material, no skin, no morphs.
- [ ] Named attach and rotation nodes are present (`wheel_L`, `banner_mount`, …).
- [ ] glTF Validator reports 0 errors. `gltf-transform inspect` was reviewed.
- [ ] GLB size is within the tier target.
- [ ] Originality check is passed: no actor, film or TV likeness. Provenance and licence are recorded truthfully.
- [ ] The registry `glb` path and `bounds` are updated, `fallback` is untouched, and `tools/validate_voyage.py` passes.
- [ ] The scene was tested in the voyage on low quality and on a phone-class GPU: no FPS collapse and no white frames.

---

## 5. Architecture

Architecture shares an idiom: an epic-age Indian royal style **imagined, not reconstructed**. It uses
plinths on stepped bases, ranks of columns with lotus or vase-and-foliage capitals, deep eaves, curved
roofs and pavilion domes (chhatri-like forms are fine as stylisation), jali screens, carved toranas, and
painted plaster over sandstone. Avoid copying any single real monument; borrow vocabulary, not buildings.
Interiors must keep the 14 m stage disc clear and fit camera heights from 1.6 m to 15 m. Ceilings are at
least 9 m.

### 5.1 `arch.hastinapura_palace`: Hastinapura royal palace (FULL)
- **Use.** This is the establishing centrepiece for the `hastinapura` location (Kuru capital). It is seen
  in aerial cranes (camera up to 120 m) and in ground-level reveals. It is also reused, under different
  light, as a darker, mourning version for `hastinapura_postwar`.
- **Scale.** About 64 m wide (X) × 30 m tall to the main finial × 42 m deep (Z). The main entrance steps
  face +Z, toward the stage, because `base_layout` places the palace at negative z.
- **Pivot.** Bottom-centre of the plinth at y = 0. The front edge of the plinth is about 21 m from the pivot along +Z.
- **Geometry & proportions.**
  - A three-tier stepped plinth, 2.4 m high in total, with a 9 m wide central stair.
  - A central audience pavilion: a rectangular mass with a curved roof and three stacked eave bands, topped
    by a gilded kalasha finial.
  - Two flanking wings, each 18 m wide, with colonnaded verandas of 12 columns each (column height
    5.5 m, diameter 0.6 m, lotus capitals).
  - Corner pavilions with small domes.
  - Shallow jali balconies on the upper storey.
  - Frieze bands carved with elephants, lotuses and riders, done as normal-map detail rather than geometry.
  - The rear of the building may be simplified because it is rarely seen.
- **Materials (PBR).**
  - Warm sandstone, roughness 0.8 and metalness 0.
  - Painted lime plaster in ivory, roughness 0.7.
  - Gilded copper finials and trims, metalness 1 and roughness 0.3.
  - Dark teak doors, roughness 0.6.
  - Vermilion and indigo painted bands on the eaves.
- **Palette.** Sandstone `#C8A57A`, ivory `#F3EAD7`, gold `#C9A24B`, vermilion accents `#B5452F`, deep teak `#4A2F1E`.
- **Pose/state.** Doors closed, with doors as separate nodes `door_main_L` and `door_main_R`. Hanging lamp
  empties `lamp_00…` sit along the veranda so the engine can attach `obj.lamp`.
- **Topology / budget.** Hero tier: ≤ 150 k triangles, ≤ 6 materials. Use a trim sheet for cornices and a
  tiling atlas for walls. Columns should be instanced in Blender (linked duplicates) and joined into the
  final mesh.
- **Prompt.**
  ```
  A grand ancient Indian royal palace imagined for the Kuru capital Hastinapura, stylised mythological
  architecture, three-tier stepped sandstone plinth with a wide central staircase, central audience
  pavilion with a curved multi-eaved roof and gilded kalasha finial, two long colonnaded wings with
  lotus-capital columns, corner pavilions with small domes, carved friezes of elephants and lotuses,
  ivory lime plaster with vermilion and indigo painted bands, gilded copper trims, dark teak doors,
  clean readable silhouette, game-ready, PBR materials, front facade facing the viewer, 64 m wide
  ```
- **Blender notes.** Block out the main masses first and check the silhouette from a 120 m aerial view and
  from a 1.6 m eye-level view at z = 0. Keep the frontage symmetrical, and give the corner pavilions slight
  asymmetric wear for life.

### 5.2 `arch.indraprastha_palace`: Indraprastha palace (FULL)
- **Use.** The centrepiece of the `indraprastha` location, the city the Pandavas built from the
  Khandava wilderness. It should feel newer, brighter and more airy than Hastinapura.
- **Scale.** 70 m wide × 34 m tall × 46 m deep. The front faces +Z (§2). Pivot at bottom-centre.
- **Geometry.**
  - Raised on a broad terrace that has a reflecting water channel along the front (geometry only, 0.3 m
    deep; the water surface is supplied by `env.lake` or `env.water`).
  - A white marble-like central hall with five pavilion roofs in a rising cascade.
  - Open loggias with slim paired columns.
  - Lattice screens throughout and roof gardens (plant planters only, no foliage meshes).
  - Forest motifs (vines, deer) in reliefs, recalling the cleared forest.
- **Materials.** Polished pale stone (white marble feel, roughness 0.35, with subtle subsurface tint in the
  albedo only), lapis-blue inlay bands, gold leaf on the finials, and light sandalwood screens.
- **Palette.** Ivory `#F3EAD7`, celestial blue `#6F8FC9`, gold `#E3C77E`, forest-light accents `#6E9C7E`.
- **Budget.** Hero tier: ≤ 150 k triangles, ≤ 6 materials.
- **Prompt.**
  ```
  A luminous ancient Indian palace city-centre imagined for Indraprastha, stylised mythological
  architecture, broad terrace with a long reflecting water channel, pale polished stone halls with a
  cascade of five pavilion roofs, airy loggias with slender paired columns, lapis-blue inlay bands,
  gold-leaf finials, sandalwood lattice screens, reliefs of vines and deer, roof-garden planters, bright
  and optimistic, clean silhouette, game-ready PBR, facade facing the viewer, 70 m wide
  ```

### 5.3 `arch.maya_sabha`: the Maya Sabha, hall of illusions (FULL)
- **Use.** The interior and exterior of the assembly hall built by the asura architect Maya, in the
  `rajasuya_sabha` and `indraprastha` scenes. The epic describes illusions: floors that look like water
  and water that looks like floor, an episode that later feeds Duryodhana's humiliation. The model should
  make that *readable*: polished crystal-like floor panels next to real shallow pools, with the same tint.
- **Scale.** Interior hall 40 m wide × 14 m tall × 52 m deep. The outer shell can be simplified. The stage
  disc (radius 14 m) is centred at the hall's local origin. Pivot at floor centre, y = 0. The entrance is at +Z and the throne end lies toward −Z, with its seat facing +Z.
- **Geometry.**
  - Two rows of 10 faceted crystal columns (octagonal shafts, 0.9 m), carrying a coffered ceiling with
    star-like inlays.
  - A floor of large square panels: alternating mirror-polished stone and inset pools 0.15 m deep. The
    pool tiles are a separate mesh `pool_tiles` so the engine can apply a water shader.
  - A raised platform at the far end at z ≈ −22.
  - Arched openings with translucent lattice.
  - Floating ornamental lotus lamps as empties `lamp_00…`.
- **Materials.** Crystal-glass columns (roughness 0.05, transmission avoided; fake it with a bright
  albedo, low roughness and a subtle emissive inner glow), polished dark-blue stone floor (roughness 0.1),
  gold coffers, and pale lapis walls.
- **Palette.** Night blue `#12152E`, celestial `#6F8FC9`, gold `#C9A24B`, ivory highlights.
- **Budget.** Hero tier: ≤ 120 k triangles, ≤ 5 materials. Emissive is allowed on the inlays (`#E3C77E`).
- **Prompt.**
  ```
  Interior of a fabled assembly hall of illusions from Indian epic, stylised mythological, two rows of
  faceted crystal columns, coffered ceiling with star inlays, floor of mirror-polished deep blue stone
  tiles alternating with shallow clear pools that look identical to the floor, raised royal platform at
  the far end, arched openings with translucent lattice, gold and lapis palette, magical but restrained,
  game-ready PBR interior, 40 m wide, 52 m deep
  ```

### 5.4 `arch.dice_hall`: the dice hall, Dyuta Sabha (FULL)
- **Use.** The `dyuta_sabha` location: the game of dice and Draupadi's humiliation. The hall must be
  claustrophobic and morally heavy, with elders seated in tiers looking on.
- **Scale.** 30 m wide × 11 m tall × 36 m deep interior. Pivot at the floor centre of the gaming area
  (local origin). The entrance is at +Z, and the royal seats sit on a dais at z ≈ −14, facing +Z toward the gaming floor.
- **Geometry.**
  - A rectangular columned hall: a 4 × 6 grid of heavy square columns with bracket capitals.
  - Low tiered seating platforms on the left and right, three steps deep.
  - A central sunken gaming floor (−0.3 m, 8 × 8 m) with an inlaid board pattern of concentric squares.
  - Heavy hanging cloth canopies, to be added as separate low-poly cloth meshes `canopy_00…`.
  - Bronze oil lamps on stands, as empties.
  - Narrow high windows that admit shafts of light.
- **Materials.** Dark lacquered wood (roughness 0.45), red-black painted pillars with gold banding,
  smoke-darkened plaster, a faded crimson cloth canopy, and an inlaid ivory and ebony game floor.
- **Palette.** Charcoal `#16161D`, deep crimson `#6E1F1A`, vermilion `#B5452F`, gold `#C9A24B`.
- **Budget.** Secondary/hero tier: ≤ 90 k triangles, ≤ 5 materials.
- **Prompt.**
  ```
  Interior of an ancient Indian royal gaming hall, stylised mythological, heavy square columns with
  carved bracket capitals, dark red-black lacquered wood with gold bands, tiered seating platforms on
  both sides, central sunken floor with an inlaid ivory-and-ebony board of concentric squares, faded
  crimson cloth canopies, bronze lamp stands, narrow high windows casting light shafts, tense and
  solemn atmosphere, game-ready PBR interior, 30 m by 36 m
  ```

### 5.5 `arch.throne_room`: royal throne room (FULL)
- **Use.** The court interiors at Hastinapura and elsewhere. The contract places the **throne dais top at
  scene position [0, 1.2, −24]**. Model this asset with its local origin at the stage centre, so that the
  dais top lands at local (0, 1.2, −24) when `base_layout` places the asset at [0, 0, 0]. If the location's
  `base_layout` uses another position, compensate there rather than in the model.
- **Scale.** 34 m wide × 13 m tall × 56 m deep (z from +20 to −36). Pivot at local origin, y = 0.
- **Geometry.**
  - A nave lined with 2 × 8 round columns (6.5 m) carrying a clerestory.
  - A stepped dais at the −Z end, 1.2 m high and 9 × 6 m, with 5 steps facing +Z. The dais has an
    `throne_mount` empty at (0, 1.2, −24) for `obj.throne`.
  - Side galleries for courtiers.
  - Long carpets as separate thin meshes.
  - A painted rear wall with a sun-disc and lotus mural (texture, not geometry).
- **Materials.** Polished sandstone floor (roughness 0.4), red sandstone columns, gilded capitals, an
  indigo-and-gold ceiling, and a crimson carpet.
- **Palette.** Sandstone `#C8A57A`, gold `#C9A24B`, crimson carpet `#7A2A22`, night-blue ceiling `#1A1D3A`.
- **Budget.** Hero tier: ≤ 100 k triangles, ≤ 5 materials.
- **Prompt.**
  ```
  Grand ancient Indian royal throne hall interior, stylised mythological, long nave lined with red
  sandstone columns with gilded lotus capitals, clerestory windows, stepped stone dais at the far end
  for a lion throne, side galleries for courtiers, long crimson carpet down the centre, indigo and gold
  painted ceiling, rear wall mural of a sun disc and lotuses, warm and majestic, game-ready PBR
  interior, 34 m wide, 56 m long
  ```

### 5.6 `arch.gurukul`: gurukul, Drona's school (FULL)
- **Use.** The `gurukul` location: the princes' training under Drona, the archery test of the bird's eye,
  and Ekalavya's story nearby. Open air, forest edge.
- **Scale.** A compound 36 m × 8 m tall × 30 m. Pivot at the centre of the training ground. Huts sit
  behind z ≈ −16, and the training field keeps the stage disc clear.
- **Geometry.**
  - Three thatched huts on mud-plaster plinths with wooden posts and woven screens.
  - An open pavilion (mandapa) with 8 timber posts for lessons.
  - A low perimeter fence of bamboo.
  - An archery range: straw butts and a tall post (target slot for `obj.fish_target` and a bird target,
    as empty `target_mount`).
  - A weapons rack with bows and spears (low-poly).
  - A fire altar ring.
- **Materials.** Golden thatch (roughness 0.95), ochre mud plaster, weathered timber, bamboo, and woven palm.
- **Palette.** Sandstone `#C8A57A`, thatch `#B38B4D`, forest `#2F4A3A`, ochre `#A0652E`.
- **Budget.** Secondary tier: ≤ 50 k triangles, ≤ 4 materials. Thatch uses alpha-tested cards sparingly,
  or better, sculpted thatch with normal maps.
- **Prompt.**
  ```
  An ancient Indian forest school compound, stylised mythological, three thatched huts on ochre mud
  plinths with timber posts and woven palm screens, an open timber lesson pavilion, bamboo fence,
  archery range with straw targets and a tall target pole, weapon rack with bows and spears, small fire
  altar, warm morning light mood, handcrafted and humble, game-ready PBR, 36 m compound
  ```

### 5.7 `arch.hermitage`: forest hermitage, ashrama (FULL)
- **Use.** Naimisharanya (the frame narration), the exile forests (`kamyaka_forest`, `dwaita_forest`), and
  the sages' retreats. Sacred calm.
- **Scale.** 20 m × 6 m × 18 m. Pivot at the centre of the clearing in front of the hut.
- **Geometry.**
  - One leaf-and-thatch hut (parnashala) on a low clay plinth.
  - A simple fire altar (vedi, square, stepped) in front.
  - A deer-skin seat mat and a water pot, as empties for `obj.water_vessel`.
  - Prayer-thread lines strung between two posts.
  - A small tulsi planter.
  - Stones arranged in a ring.
- **Materials.** Leaf thatch, clay plaster, rough bark posts, river stones, and soft moss.
- **Palette.** Forest `#2F4A3A`, forest-light `#6E9C7E`, clay `#9C6B43`, ivory smoke.
- **Budget.** Secondary tier: ≤ 30 k triangles, ≤ 3 materials. Instancing: false.
- **Prompt.**
  ```
  A serene forest hermitage from Indian epic, stylised mythological, a single leaf-and-thatch hut on a
  low clay plinth, square stepped fire altar in front, simple seat mat, clay water pots, strung prayer
  threads between two wooden posts, small sacred basil planter, ring of river stones, moss and soft
  light, humble and peaceful, game-ready PBR, 20 m clearing
  ```

### 5.8 `arch.city_gate`: city gate, gopura-like torana (FULL)
- **Use.** Arrivals and departures: Hastinapura, Indraprastha, Virata, Dwaraka. It is a `door_open`
  transition subject.
- **Scale.** 22 m wide × 20 m tall × 9 m deep. The gate opening is 6 m wide × 9 m high. Pivot at
  bottom-centre of the opening. The passage runs along Z. The ornate outer facade faces +Z (toward the
  stage and the arriving camera; see §2), and the plainer city side faces −Z.
- **Geometry.**
  - A massive stepped tower over the gateway with 4 diminishing storeys, finials at the top, and
    guardian niches flanking the opening (empty niches; no specific deity sculpture).
  - Wall stubs left and right, 6 m each, that connect to `arch.city_walls`.
  - Two heavy timber gate leaves as separate nodes `gate_L` and `gate_R`, with pivots at their hinges
    for the opening animation.
  - Bronze studs on the leaves.
  - Banner poles on top, as `banner_mount_L` and `banner_mount_R` empties.
- **Materials.** Sandstone blocks, painted plaster bands, teak gates with bronze studs (metalness 1, roughness 0.4).
- **Budget.** Secondary tier: ≤ 50 k triangles, ≤ 4 materials.
- **Prompt.**
  ```
  A monumental ancient Indian city gateway, stylised mythological, stepped four-storey tower over a
  tall arched passage, empty guardian niches flanking the opening, short fortified wall stubs on both
  sides, two massive teak gate leaves studded with bronze, finials and banner poles on top, sandstone
  with painted plaster bands, imposing and welcoming, game-ready PBR, 22 m wide, 20 m tall
  ```

### 5.9 Other architecture slots (compact briefs)

Each of these follows §2 and the §5 idiom. Bottom-centre pivot, front faces +Z (§2), secondary tier unless noted.

| Slot | Brief (geometry · scale · materials · budget) | Prompt seed |
|---|---|---|
| `arch.pillared_corridor` | A straight colonnade segment that tiles end-to-end: 2 × 6 columns, flat beamed roof, 24 m long × 7 m tall × 6 m wide. Module length 24 m, pivot at one end so copies chain along −Z. Sandstone and painted beams. ≤ 25 k tris. | "ancient Indian pillared corridor module, lotus-capital columns, beamed stone ceiling, warm sandstone, tileable" |
| `arch.sabha_hall` | A generic assembly hall (Hastinapura court, Virata, Upaplavya councils). An open-sided pavilion 28 × 10 × 34 m on a plinth with 3 × 7 columns, a pyramidal tiered roof, and low seating platforms. ≤ 60 k tris. | "open-sided royal assembly pavilion, tiered pyramidal roof, columns on a stone plinth, seating platforms" |
| `arch.royal_courtyard` | A palace courtyard 40 × 40 m (open sky): arcaded perimeter 6 m tall, central stepped tank or fountain basin, planters, paved stone. Keep the stage disc clear. ≤ 50 k tris. | "ancient Indian palace courtyard, arcaded perimeter, central stepped water basin, paved sandstone" |
| `arch.temple` | A small shrine 10 × 16 × 12 m: stepped plinth, sanctum with a curvilinear tower, porch with 4 columns. No specific deity image (the sanctum is dark). ≤ 30 k tris. | "small ancient Indian stone shrine, curvilinear tower, columned porch, stepped plinth, sanctum in shadow" |
| `arch.battle_camp` | A war-camp tent cluster at Kurukshetra, 40 × 8 × 40 m: one large command tent with a striped canopy, 6 smaller tents, weapon racks, cooking fire rings, and banner poles as empties. Canvas and timber. ≤ 40 k tris. | "ancient Indian war camp, large striped command tent, smaller canvas tents, weapon racks, banner poles" |
| `arch.chariot_shelter` | A timber shed for 2 chariots, 12 × 5 × 8 m, with a thatched roof on posts and an open front. ≤ 10 k tris. | "open timber chariot shed, thatched roof on carved posts, packed earth floor" |
| `arch.royal_balcony` | A projecting jharokha-style balcony with canopy, 6 × 5 × 3 m, which attaches to a facade. Pivot at the rear-bottom-centre (the wall plane). Gilded brackets, lattice. ≤ 12 k tris. | "ornate projecting palace balcony, curved canopy, carved brackets, lattice screens, gilded trim" |
| `arch.lac_house` | The house of lac at Varanavata: a handsome mansion that hides its flammable construction. 24 × 10 × 18 m, glossy resin-lacquered timber with an amber sheen and an ornamented facade, plus a tunnel mouth hidden under a floor slab (separate node `tunnel_hatch`). Show no burning damage in the model; fire is a separate effect (`env.fire`). ≤ 40 k tris. | "elegant ancient Indian timber mansion with glossy amber lacquered walls, ornate facade, subtly unsettling" |
| `arch.swayamvara_arena` | Draupadi's swayamvara at Panchala: a circular arena 50 m in diameter, with tiered pavilions for kings around it, a central tall pole and target platform (`target_mount` empty for `obj.fish_target`), and a water basin beneath it for the reflection test as described in the epic. Leave the arena centre open. ≤ 80 k tris. | "circular royal tournament arena, tiered canopied pavilions, central tall target pole above a water basin" |
| `arch.dwaraka_city` | Distant city massing for Dwaraka by the western sea: a 300 m wide skyline of white and gold towers on a coastal rise, sea walls and harbour, seen only from far away. ≤ 60 k tris. Low-detail textures. | "distant ancient Indian coastal city of white and gold towers, sea walls, harbour, golden-hour skyline" |
| `arch.yajna_altar` | A ritual fire altar: stepped brick vedi 3 × 0.8 × 3 m, kusha-grass border, ladles, and a `fire_mount` empty for `env.fire`. ≤ 6 k tris. | "Vedic stepped brick fire altar, kusha grass border, wooden ladles, ceremonial" |
| `arch.city_walls` | A tileable fortification segment 30 m long × 12 m tall × 6 m thick with battlements, a bastion every 2nd module, and a moat edge. Pivot at one end so copies chain. Sandstone. ≤ 15 k tris per module. | "ancient Indian fortified city wall module, battlements, bastion, sandstone, tileable" |

---

## 6. Characters

**Shared character brief.**
- **Tier.** Principal ≤ 30 k triangles, ≤ 2 materials (body 2048², ornaments and weapons 1024²).
  Secondary ≤ 8 k. Generic ≤ 6 k, one material.
- **Delivery.** A **static posed mesh** in the registry's `fallback.params.pose` (standing, seated, archer,
  charioteer, mace, kneeling, lying, walking or blessing), since the engine animates idle_sway and breathe
  procedurally. Optionally also deliver a rigged A-pose version with a humanoid skeleton (`<id>_rig.glb`)
  for future work. The registry points to the static one.
- **Orientation & pivot.** +Y up, facing +Z (§2; confirm against the `figure` factory and the open
  registry `forward` question noted there), pivot between the feet at y = 0. The height matches the
  registry `visual_identity.height`; the heights below are suggestions.
- **Attach points (empties).** `hand_R`, `hand_L`, `back`, `head_top`. Weapons from the `obj.*` slots
  (e.g. `obj.gandiva`) are attached by the engine, so sculpt hands to grip them but keep weapons as separate
  nodes or omit them.
- **Style.** Faces are idealised and *sculptural*, not portrait-real: almond eyes, serene brows and
  stylised hair masses. Faces must be distinct from one another through silhouette, hair, ornament and
  colour, never through a real person's features. Skin uses a painted-statue PBR (roughness 0.55–0.7,
  subtle sheen) in a range of brown complexions. Krishna and Draupadi are described as dark-complexioned
  in the epic; keep that and do not lighten them.
- **Registry.** Use the slot's `fallback.params` colours (robe, sash, skin) as the base palette. The values
  below are suggestions, and the registry is the source of truth where they differ.
- **Common prompt suffix.** Append to every character prompt:
  `original stylised mythological character design, sculptural painted-statue look, idealised features, game-ready PBR, clean topology, full body, facing the viewer` + the NEGATIVE prompt from §1.

### 6.1 `char.krishna`: Krishna (FULL)
- **Role.** Prince of the Yadavas, Arjuna's friend and charioteer, speaker of the Bhagavad Gita. The voyage
  shows him as counsellor and divine presence, never as spectacle.
- **Height / proportions.** 1.82 m. Slender, graceful, relaxed contrapposto, a gentle tribhanga sway at rest.
- **Iconography (traditional).**
  - Dark, blue-black complexion (shyama) with an indigo sheen.
  - Yellow silk lower garment (pitambara) with an ochre and gold border.
  - A single peacock feather set in a small gold crown band.
  - A garland of forest flowers (vanamala) to the knees.
  - A bamboo flute tucked into the sash in non-battle scenes.
  - Gold armlets and ear ornaments.
- **Pose.** The registry pose (charioteer in the Kurukshetra and Gita scenes: seated slightly forward,
  reins in both hands; blessing elsewhere: right hand raised in abhaya, palm out).
- **Materials.** Raw-silk yellow (roughness 0.6, slight sheen), hammered gold (metalness 1, roughness
  0.35), flower garland (vertex-coloured blossoms, low-poly), skin roughness 0.55.
- **Palette.** Skin `#2B3550`, silk `#D9A93A`, border `#9A6A1E`, gold `#C9A24B`, garland `#C2456B` / `#F3EAD7`, feather teal `#1F7A74`.
- **Budget / pivots.** 30 k tris. `hand_R` for the flute or reins, `hand_L`. Peacock feather as a separate
  node `feather` for flutter.
- **Prompt.**
  ```
  Krishna from the Indian epic as an original stylised mythological figure, slender graceful young
  prince, deep blue-black skin with an indigo sheen, serene gentle half-smile, yellow raw-silk dhoti with
  ochre and gold border, single peacock feather in a small gold crown band, long garland of forest
  flowers, gold armlets and ear ornaments, bamboo flute tucked in the sash, relaxed graceful stance,
  height 1.82 m
  ```
- **Avoid.** Any face or costume recognisable from screen adaptations, glitter, plastic sheen, baby-face, and feminised caricature.

### 6.2 `char.arjuna`: Arjuna (FULL)
- **Height.** 1.86 m. A lean, long-limbed archer. Long arms are part of his description; be subtle about it.
- **Design.**
  - Ivory and indigo layered war garb.
  - A fitted leather and brass cuirass with a sun-ray tooling pattern.
  - A white sash. Hair in a high knot bound with a plain gold band. Simple ear-rings.
  - Bracer on the left forearm (bowstring guard) and finger guard on the right hand.
  - Two quivers on the back, as a separate node `quivers`.
- **Pose.** archer: Gandiva drawn, string at the cheek, weight on the back foot. Or standing with the bow
  resting. In the Gita scene: kneeling or seated in the chariot, bow set down (registry pose decides).
- **Materials.** Cotton (roughness 0.8), leather (0.6), brass (metalness 1, roughness 0.4).
- **Palette.** Skin `#8A5A3C`, indigo `#2C3A6B`, ivory `#F3EAD7`, brass `#B08A3E`.
- **Attach.** `hand_L` holds the bow grip (obj.gandiva origin) and `hand_R` the string. Budget 30 k.
- **Prompt.**
  ```
  Arjuna the archer prince from the Indian epic, original stylised mythological design, tall lean
  long-limbed warrior, focused calm expression, ivory and indigo layered war garments, fitted leather
  cuirass with brass sun-ray tooling, white sash, hair in a high knot with a plain gold band, leather
  bracer on the bow arm, twin quivers on the back, drawing a long recurved bow in a poised archer
  stance, height 1.86 m
  ```

### 6.3 `char.bhima`: Bhima (FULL)
- **Height.** 2.0 m. The broadest silhouette in the cast: a barrel chest, thick neck and powerful but not
  bodybuilder-exaggerated arms.
- **Design.** Earth-ochre and saffron dhoti, a wide leather belt with bronze plates, heavy bronze armlets,
  short curly hair tied back, and a short beard. Bare chest with a leather shoulder strap.
- **Pose.** mace: a gada resting on the right shoulder and the left fist on the hip.
- **Palette.** Skin `#7A4A2E`, ochre `#A0652E`, saffron `#D98A2B`, bronze `#8C6239`.
- **Attach.** `hand_R` holds `obj.mace` (origin at the grip). Budget 30 k.
- **Prompt.**
  ```
  Bhima the mighty Pandava from the Indian epic, original stylised mythological design, towering broad
  barrel-chested warrior, short curly hair tied back, short beard, earth-ochre and saffron dhoti, wide
  leather belt with bronze plates, heavy bronze armlets, bare chest with a leather shoulder strap,
  resting a massive mace on his shoulder, grounded confident stance, height 2.0 m
  ```

### 6.4 `char.yudhishthira`: Yudhishthira (FULL)
- **Height.** 1.80 m. Upright, composed and slightly reserved: a king defined by dharma and doubt.
- **Design.** White and pale-gold royal garments, a modest gold crown in court scenes (as a separate node
  `crown`, so the exile variant can hide it), an upper cloth draped over the left shoulder, and a short
  ceremonial spear in war scenes (separate node).
- **Pose.** standing, hands loosely joined in front. Or seated (dice hall).
- **Palette.** Skin `#8C5E40`, white `#EFE8DA`, pale gold `#E3C77E`, accent `#6F8FC9`.
- **Prompt.**
  ```
  Yudhishthira the eldest Pandava king from the Indian epic, original stylised mythological design,
  upright composed and thoughtful man, short neat beard, white and pale-gold royal garments, upper cloth
  over one shoulder, modest gold crown, hands loosely joined, calm searching expression, height 1.80 m
  ```

### 6.5 `char.nakula`: Nakula (FULL)
- **Height.** 1.78 m. Celebrated for his beauty and his skill with horses and the sword.
- **Design.** Forest-green and silver garments, a light scale vest, a curved sword (sheathed at the hip,
  separate node) and a round shield on the back. A horse-head motif on the belt buckle.
- **Pose.** standing, hand on the sword hilt.
- **Palette.** Skin `#8F6043`, green `#2F5A44`, silver `#B8BCC2`, gold trim `#C9A24B`.
- **Prompt.**
  ```
  Nakula the Pandava twin from the Indian epic, original stylised mythological design, handsome
  athletic young warrior, forest-green and silver garments, light scale vest, curved sword at the hip,
  round shield on his back, horse-head belt buckle, hand resting on the sword hilt, height 1.78 m
  ```

### 6.6 `char.sahadeva`: Sahadeva (FULL)
- **Height.** 1.76 m. The youngest Pandava, wise in astronomy and counsel, quieter in bearing than his twin.
- **Design.** Deep teal and indigo garments, a scholar's upper cloth, a short sword, and a bundle of
  palm-leaf notes at the belt (separate node).
- **Pose.** standing, one hand raised slightly as if reckoning.
- **Palette.** Skin `#8A5D41`, teal `#1F5E63`, indigo `#2C3A6B`, ivory `#F3EAD7`.
- **Prompt.**
  ```
  Sahadeva the youngest Pandava from the Indian epic, original stylised mythological design, quiet
  scholarly young warrior, deep teal and indigo garments, draped upper cloth, short sword, bundle of
  palm-leaf notes at the belt, thoughtful expression, one hand raised as if calculating, height 1.76 m
  ```

### 6.7 `char.draupadi`: Draupadi (FULL)
- **Height.** 1.70 m. A queen born from the sacrificial fire, described in the epic as dark-complexioned
  (her name Krishnaa means "the dark one"). Her bearing is commanding and dignified. She is never
  sexualised and never shown as a victim in pose.
- **Design.**
  - A deep vermilion and crimson sari with a gold border carrying a subtle flame motif (texture), and a
    gold waist chain.
  - Hair: deliver two variants as separate nodes. `hair_bound` is a braided bun for the early and court
    scenes. `hair_unbound` is long open hair, for after the dice game, recalling her vow (the vow is
    widely told; details differ between versions).
- **Pose.** standing, chin raised, one hand at the heart.
- **Palette.** Skin `#4E3326`, vermilion `#B5452F`, crimson `#7A1F1A`, gold `#C9A24B`.
- **Prompt.**
  ```
  Draupadi the fire-born queen from the Indian epic, original stylised mythological design, dignified
  commanding woman with dark brown skin, steady intense gaze, deep vermilion and crimson sari with a gold
  border of subtle flame motifs, gold waist chain and modest jewellery, long dark hair, chin raised, one
  hand at her heart, modest and regal, height 1.70 m
  ```

### 6.8 `char.bhishma`: Bhishma (FULL)
- **Height.** 1.90 m. Very old but unbowed, the grand-sire bound by his vow.
- **Design.** A long white beard and hair, white robes, and in war, silver-white lamellar armour over
  them. A tall bow (separate node). A palm-tree banner is associated with him in the epic; that belongs to
  the `obj.banner` texture variant, not to this model.
- **Pose.** standing, bow grounded. A **lying** variant (registry pose `lying`) is supine, arms at rest,
  head slightly raised, and serene. It is used with `obj.bed_of_arrows`, so the figure carries no visible
  arrows or wounds.
- **Palette.** Skin `#8E6A52`, white `#ECE6DA`, silver `#B8BCC2`, gold `#C9A24B`.
- **Prompt.**
  ```
  Bhishma the venerable grandsire from the Indian epic, original stylised mythological design, very old
  tall unbowed warrior with long flowing white beard and hair, white robes under silver-white lamellar
  armour, grave noble expression, holding a tall bow grounded at his side, height 1.90 m
  ```

### 6.9 `char.drona`: Drona (FULL)
- **Height.** 1.80 m. A Brahmin teacher of arms: scholarly and martial at once.
- **Design.** A grey beard, a shaven head with a topknot (shikha), the sacred thread, saffron-ochre robes,
  a bronze chest plate added in war, and a bow.
- **Pose.** standing, teaching gesture (index finger raised), or archer.
- **Palette.** Skin `#8B5F45`, saffron `#C9772B`, ochre `#A0652E`, bronze `#8C6239`.
- **Prompt.**
  ```
  Drona the warrior-teacher from the Indian epic, original stylised mythological design, older lean
  Brahmin master of arms, grey beard, shaven head with a topknot, sacred thread across the chest,
  saffron and ochre robes with a bronze chest plate, holding a bow, raising a finger as if teaching,
  stern and wise, height 1.80 m
  ```

### 6.10 `char.karna`: Karna (FULL)
- **Height.** 1.88 m. A great archer of solar birth, raised by a charioteer family. His bearing should be
  noble melancholy.
- **Design.** Golden-bronze skin sheen. The famous innate armour and ear-rings (kavacha and kundala) are
  rendered *suggestively*: a fitted breastplate with fine golden scale relief and sun-disc ear ornaments.
  Their description varies between versions, so keep it stylised. Maroon and gold garments, sun motifs on
  the belt, and a bow (separate node).
- **Pose.** archer or standing with bow.
- **Palette.** Skin `#9A6A3E`, gold `#D8B04A`, maroon `#5E1F24`, sun accents `#E3C77E`.
- **Prompt.**
  ```
  Karna the solar-born archer from the Indian epic, original stylised mythological design, tall noble
  warrior with golden-bronze skin sheen, melancholy proud expression, fitted breastplate with fine
  golden scale relief, sun-disc ear ornaments, maroon and gold garments with sun motifs, holding a great
  bow, height 1.88 m
  ```

### 6.11 `char.duryodhana`: Duryodhana (FULL)
- **Height.** 1.86 m. The proud, powerful Kaurava crown prince, regal and wounded in pride. He is not a cartoon villain.
- **Design.** Rich crimson and black garments with gold brocade (texture), an imposing tall crown
  (separate node), broad gold collar, heavy armlets, and a mace. A serpent emblem on the belt (his
  serpent banner is told in the epic).
- **Pose.** mace or standing with arms crossed.
- **Palette.** Skin `#865838`, crimson `#7A1F1A`, black `#16161D`, gold `#C9A24B`.
- **Prompt.**
  ```
  Duryodhana the Kaurava crown prince from the Indian epic, original stylised mythological design,
  powerful proud warrior king, strong jaw, intense gaze, rich crimson and black garments with gold
  brocade, tall imposing crown, broad gold collar, heavy armlets, serpent emblem belt, mace in hand,
  regal and formidable, height 1.86 m
  ```

### 6.12 `char.shakuni`: Shakuni (FULL)
- **Height.** 1.72 m. The prince of Gandhara and master of dice. He is lean and watchful, with intelligence
  in the eyes. Show no deformity and no ethnic caricature.
- **Design.** Layered wool garments in northern style (plum and olive), a fur-trimmed shawl, rings, and a
  small embroidered cap. A pair of oblong dice in his right hand, as a separate node or via `obj.dice`.
- **Pose.** standing, dice held up between his fingers, the other hand behind his back.
- **Palette.** Skin `#8E6648`, plum `#4A2740`, olive `#5A5A32`, gold `#C9A24B`.
- **Prompt.**
  ```
  Shakuni the prince of Gandhara from the Indian epic, original stylised mythological design, lean
  watchful older nobleman, sharp intelligent eyes, neat greying beard, layered plum and olive wool
  garments with a fur-trimmed shawl, small embroidered cap, rings on his fingers, holding a pair of
  oblong dice between his fingers, one hand behind his back, height 1.72 m
  ```

### 6.13 `char.kunti`: Kunti (FULL)
- **Height.** 1.66 m. The mother of the Pandavas (and of Karna): resilient, dignified and burdened.
- **Design.** Pale saffron and ivory garments for the early scenes. A widow's white variant (texture swap
  or second material) for later ones. A simple head-covering drape and minimal jewellery.
- **Pose.** standing or blessing.
- **Palette.** Skin `#8A5E44`, ivory `#F3EAD7`, pale saffron `#E3B270`, white `#ECE6DA`.
- **Prompt.**
  ```
  Kunti the queen mother from the Indian epic, original stylised mythological design, mature dignified
  woman, resilient gentle expression, pale saffron and ivory draped garments with a head-covering drape,
  minimal jewellery, hand raised in blessing, height 1.66 m
  ```

### 6.14 `char.gandhari`: Gandhari (FULL)
- **Height.** 1.68 m. The queen who chose to share her blind husband's darkness by binding her eyes.
- **Design.** A folded silk blindfold (deep indigo) bound over her eyes and knotted behind. Deep indigo and
  ash-grey garments, a silver ornament, and a regal posture.
- **Pose.** standing, hands joined. Or seated beside Dhritarashtra.
- **Palette.** Skin `#8B6247`, indigo `#2A2D55`, ash grey `#7A7A82`, silver `#B8BCC2`.
- **Prompt.**
  ```
  Gandhari the blindfolded queen from the Indian epic, original stylised mythological design, regal
  composed woman with a folded deep indigo silk band bound over her eyes, indigo and ash-grey draped
  garments, silver ornaments, hands joined, solemn and strong, height 1.68 m
  ```

### 6.15 `char.abhimanyu`: Abhimanyu (FULL)
- **Height.** 1.72 m. Arjuna and Subhadra's son, sixteen at Kurukshetra: bright, brave and youthful.
- **Design.** Light sky-blue and gold armour, a short tunic, a youthful hair knot, a light bow and a sword.
  A chariot-wheel attribute for the Chakravyuha stand is optional, as a separate node. Show no wounds.
- **Pose.** archer or walking (advancing).
- **Palette.** Skin `#8F6145`, sky blue `#6F8FC9`, gold `#E3C77E`, white `#F3EAD7`.
- **Prompt.**
  ```
  Abhimanyu the young warrior from the Indian epic, original stylised mythological design, brave
  sixteen-year-old prince, bright determined face, light sky-blue and gold armour, short tunic,
  youthful hair knot, light bow in hand, sword at the hip, striding forward, height 1.72 m
  ```

### 6.16 `char.vidura`: Vidura (FULL)
- **Height.** 1.78 m. A minister of wisdom and plain speech at the Kuru court.
- **Design.** Simple ivory and pale-grey robes, no crown, a shawl, a palm-leaf scroll in the hand (separate
  node), and a calm, kindly face with a short grey beard.
- **Pose.** standing, speaking gesture.
- **Palette.** Skin `#8A5D42`, ivory `#F3EAD7`, pale grey `#A8A6A0`, sandalwood `#C8A57A`.
- **Prompt.**
  ```
  Vidura the wise minister from the Indian epic, original stylised mythological design, calm kindly
  man with a short grey beard, simple ivory and pale-grey robes with a shawl, no crown, holding a
  palm-leaf scroll, open speaking gesture, height 1.78 m
  ```

### 6.17 Other character slots (compact briefs)

Secondary tier (≤ 8 k tris) unless marked. Every prompt also gets the common suffix and the NEGATIVE prompt.

| Slot | Design brief (height · look · pose) |
|---|---|
| `char.dhritarashtra` | 1.84 m. The blind Kuru king: heavy, regal, uncertain; eyes closed; maroon and gold robes and crown; seated on a throne. |
| `char.pandu` | 1.82 m. Pandu ("the pale"): fair-ash complexion, archer king, white and blue garments, bow; standing. |
| `char.madri` | 1.66 m. Queen of Madra, mother of the twins: green and gold garments; standing. |
| `char.shantanu` | 1.84 m. Kuru king of the old generation: grey-streaked beard, blue and gold, crown; standing. |
| `char.ganga_devi` | 1.80 m. The river goddess: flowing translucent blue-white drapery (opaque mesh with gradient texture, no transmission), water-ripple motifs, lotus in hand; may stand on a stylised makara (optional separate node). `emissive` faint `#BFD6F2`. |
| `char.satyavati` | 1.66 m. Ferry-girl turned queen mother: river-blue and ivory garments; later regal. Standing. |
| `char.vyasa` | 1.76 m. The sage-composer: dark complexion, matted hair and long beard, bark-cloth and deer-skin, palm-leaf and stylus; seated. (The Ganesha-as-scribe story is a later addition; do not bake Ganesha into this model.) |
| `char.vaishampayana` | 1.74 m. Vyasa's disciple and reciter: ivory robes, sacred thread, palm-leaf bundle; seated, reciting. |
| `char.ugrashravas` | 1.74 m. The bard (Sauti) narrating at Naimisharanya: travelling garments, shawl, staff; seated or standing, speaking gesture. |
| `char.janamejaya` | 1.80 m. The Kuru king whose sacrifice frames the telling: crown, white and gold ritual garb; seated. |
| `char.ashwatthama` | 1.84 m. Drona's son, a fierce warrior: a gem on his forehead (subtle emissive `#E3C77E`), dark armour; standing. No gore in any variant. |
| `char.kripa` | 1.78 m. Teacher of the princes: elderly, ochre robes, bow; standing. |
| `char.drupada` | 1.82 m. King of Panchala: bearded, saffron and red royal garments, crown; standing. |
| `char.dhrishtadyumna` | 1.84 m. Fire-born commander of the Pandava army: ember-orange armour accents, sword; standing. |
| `char.shikhandi` | 1.78 m. The warrior reborn (Amba's story continued): dignified armour in muted rose and steel, bow; standing. Respectful portrayal. |
| `char.amba` | 1.68 m. The princess wronged by abduction and refusal: austere ascetic garments of later life, unbound hair; standing, resolute. |
| `char.virata_king` | 1.82 m. King of the Matsyas: green and gold, crown; seated. |
| `char.uttara` | 1.74 m. Prince of Virata: light armour, nervous then brave; charioteer or standing. |
| `char.uttara_kumari` | 1.62 m. Princess of Virata, later Abhimanyu's wife: pink and gold garments; standing. |
| `char.shalya` | 1.82 m. King of Madra, Karna's charioteer: bearded, olive and bronze; charioteer pose. |
| `char.sanjaya` | 1.76 m. The king's charioteer-narrator with divine sight: pale robes, subtle celestial-blue emissive trim; standing, eyes half-closed. |
| `char.ekalavya` | 1.72 m. A forest-dwelling archer: bark and leather garments, feathers, bow. Hands whole and no injury shown; the thumb episode is narrated, not depicted. Kneeling before a clay image (the image is a separate prop). |
| `char.jayadratha` | 1.80 m. King of the Sindhus: dark armour with blue accents; standing. |
| `char.dushasana` | 1.84 m. Duryodhana's brother: crimson and black, mace; standing. Not a caricature. |
| `char.ghatotkacha` | 2.1 m, the top of the contract's figure range (`visual_identity.height` is capped at 2.1 by the voyage schema; the registry figure is 2.1 m). The epic calls him gigantic, so a larger on-screen size is a data/schema decision, not a modelling one. Son of Bhima and Hidimbi, a giant of rakshasa heritage: imposing but not monstrous, dark earth tones, bronze ornaments, huge mace; standing. Principal tier budget. |
| `char.balarama` | 1.92 m. Krishna's elder brother: fair complexion, deep-blue garments, plough and mace attributes; standing. |
| `char.subhadra` | 1.66 m. Krishna's sister, Arjuna's wife: yellow and blue garments; standing. |
| `char.vikarna` | 1.80 m. The Kaurava who objected in the dice hall: lighter crimson garments, open speaking gesture; standing. |
| `char.yuyutsu` | 1.80 m. The Kaurava half-brother who crossed to the Pandavas: muted garments mixing both factions' colours; standing. |
| `char.kichaka` | 1.90 m. Commander at Virata: heavy armour, imposing; standing. |
| `char.parikshit` | 1.78 m. Abhimanyu's son, later king: youthful crown, white and gold; seated. |
| `char.hidimbi` | 1.80 m. A forest-dwelling rakshasi who married Bhima: forest garments, strong and graceful, wildflowers; standing. Not monstrous. |
| `char.ulupi` | 1.70 m. A Naga princess: teal and silver scaled garment pattern, serpent-hood motif crown; standing. |
| `char.chitrangada` | 1.72 m. Warrior princess of Manipura: armour and wrapped skirt, spear; standing. |
| `char.sage_generic` | 1.74 m. Generic rishi: matted hair, beard, bark-cloth, staff and water pot. **Instanced**, ≤ 6 k tris, one material. |
| `char.soldier_generic` | 1.76 m. Foot soldier: quilted cotton armour, round shield, spear. **Instanced**, ≤ 3 k tris (crowds), one material, team colour via vertex colour or registry tint. |
| `char.courtier_generic` | 1.74 m. Courtier: draped upper cloth, turban, folded hands. **Instanced**, ≤ 4 k tris. |
| `char.archer_generic` | 1.76 m. Archer: quiver, bow, light armour. **Instanced**, ≤ 4 k tris. |
| `char.yaksha` | 2.2 m. The nature spirit of the lake (Yaksha Prashna): stylised, otherworldly but benign, lotus-and-water ornament, faint emissive eyes `#BFD6F2`; standing above water. Secondary tier. |

---

## 7. Objects

### 7.1 `obj.krishna_chariot`: Krishna's chariot with four white horses (FULL)
- **Use.** The chariot of the Gita scene and the war, driven by Krishna with Arjuna as warrior. The epic
  and later tradition describe white horses; the names and details differ between passages and versions,
  so keep the design non-literal.
- **Scale.**
  - The chariot body plus wheels is 3.0 m long, 2.0 m wide (wheel to wheel) and 2.2 m tall without the
    flagpole.
  - The flagpole rises to 7 m.
  - Overall with the 4 horses: about 7.4 m long (Z) × 3.2 m wide.
  - Each horse is about 2.4 m long and 1.65 m at the withers.
- **Orientation / pivot.** The horses lead toward +Z, the asset's front (§2). Pivot at the ground centre under the axle.
- **Required nodes.**
  - `wheel_L` and `wheel_R`, each pivoted at the hub and spinning about X (for the `roll` animation).
  - `horse_0` … `horse_3`, each pivoted at the hooves' centre so the head and neck can bob.
  - `banner_mount` (empty at the top of the flagpole) for `obj.kapidhvaja_banner`.
  - `seat_charioteer`, an empty at the low front seat for `char.krishna` in pose charioteer.
  - `stand_warrior`, an empty on the rear platform for `char.arjuna`.
  - `reins`, a separate thin mesh.
- **Geometry.**
  - Wheels 1.5 m in diameter with 12 spokes, iron-rimmed and gilded.
  - A carved timber car with a curved breastwork and bronze lion-head finials.
  - A single draught pole with a yoke for four horses abreast.
  - Quiver holders on the sides.
  - Horses: elegant, long-necked, white, with gold harness, plumes and bells.
- **Materials.**
  - Teak with gold leaf (wood roughness 0.55; gold metalness 1, roughness 0.3).
  - Bronze fittings.
  - Horse coat: off-white, roughness 0.6, with a soft painted shadow gradient in the albedo.
  - Harness: crimson leather with gold.
- **Palette.** Gold `#C9A24B`, teak `#5A3A22`, horse white `#EDE7DA`, harness crimson `#7A1F1A`, bronze `#8C6239`.
- **Budget.** Hero prop tier: ≤ 60 k tris for the whole set (car 20 k, horses 9 k each), ≤ 2 materials
  (car + horses). Horses stay unrigged; the engine animates node transforms.
- **Prompt.**
  ```
  An ornate ancient Indian war chariot from the epic, stylised mythological, two large twelve-spoked
  gilded wheels with iron rims, carved teak car with curved front breastwork and bronze lion-head
  finials, low charioteer seat at the front and standing platform behind, tall flagpole at the rear,
  single pole and yoke drawn by four elegant white horses abreast with crimson and gold harness and
  plumes, noble and swift, game-ready PBR, 7.4 m long overall
  ```

### 7.2 `obj.gandiva`: the Gandiva bow (FULL)
- **Use.** Arjuna's divine bow, which the epic says came to him through Agni at the Khandava forest. It is
  his primary attribute (attached at `hand_L`).
- **Scale.** 1.75 m tip to tip when strung, 0.12 m deep at the grip. Origin at the **grip centre**. The
  bow lies in the YZ plane, with the string toward −Z (toward an archer who faces +Z) and the belly facing +Z.
- **Geometry.** A long, elegant reflex-recurve composite bow, with a laminated horn and wood look in
  layers, gold-wrapped grip, gilded tips (siyahs) shaped like stylised makara heads, and a single string
  (separate node `string`). Optionally add a morph target `draw` (string pulled 0.7 m back, limbs flexed)
  so `draw_bow` can blend it.
- **Materials.** Dark lacquered horn-wood (roughness 0.35), gold wire (metalness 1), and a faint emissive
  edge inlay `#E3C77E` (strength low) to signal the divine.
- **Budget.** ≤ 8 k tris, 1 material, 1024².
- **Prompt.**
  ```
  A divine ancient Indian composite bow, stylised mythological, long elegant reflex-recurve limbs of
  dark lacquered horn and wood laminations, gold-wrapped grip, gilded tips shaped like stylised makara
  heads, fine gold inlay along the limbs with a faint inner glow, single taut string, 1.75 m tall,
  game-ready PBR prop
  ```

### 7.3 `obj.sudarshana`: the Sudarshana chakra (FULL)
- **Use.** Krishna's discus. It appears rarely (the Shishupala episode, moments of divine revelation).
  Use it as a luminous symbol, not a gore weapon.
- **Scale.** 0.6 m in diameter, 0.05 m thick. Origin at the centre. It spins about its local Z axis, with
  the disc face toward +Z (for the `rotate` animation).
- **Geometry.**
  - A central hub with a lotus rosette.
  - Two concentric rings of spokes (8 inner, 16 outer).
  - An outer rim with many small serrations. The number varies in later iconography, so keep it
    decorative, e.g. 48.
  - Flame tongues around the rim as a separate node `flames`, made of thin emissive cards.
- **Materials.** Polished gold (metalness 1, roughness 0.2). Emissive rim `#F2C14E`, strength 1.5–3.
  Flame cards are additive-looking: emissive with alpha.
- **Budget.** ≤ 6 k tris, 1–2 materials.
- **Prompt.**
  ```
  A divine spinning discus from Indian mythology, stylised, polished gold, central lotus rosette hub,
  two concentric rings of spokes, serrated outer rim with a radiant golden glow and subtle flame tongues,
  sacred and luminous rather than violent, 0.6 m diameter, game-ready PBR prop
  ```

### 7.4 `obj.panchajanya`: the Panchajanya conch (FULL)
- **Use.** Krishna's conch, sounded at the start of battle (it pairs with the `conch` audio cue).
  Hand-held (`hand_R`) or displayed on a stand.
- **Scale.** 0.32 m long. Origin at the grip, the point where the hand holds the body near the mouth. The
  apex points +Y in the blowing pose, and the mouth opening faces +Z.
- **Geometry.** A sacred chank (shankha) shell form: a spiral body with a smooth glossy surface, a gold
  cap on the blowing end, and a gold filigree band with tassels (separate node) at the aperture. The
  aperture lip is sculpted, not textured.
- **Materials.** Porcelain-white shell (roughness 0.25, faint pink-ivory gradient `#F4E9DC`→`#EBCFC2`),
  gold (metalness 1).
- **Budget.** ≤ 5 k tris, 1 material.
- **Prompt.**
  ```
  A sacred Indian conch shell (shankha), stylised mythological, glossy porcelain-white spiral shell
  with soft pink-ivory gradient, gold mouthpiece cap, delicate gold filigree band with small tassels,
  revered divine object, 0.32 m long, game-ready PBR prop
  ```

### 7.5 `obj.manuscript`: palm-leaf manuscript, pothi (FULL)
- **Use.** The opening sequence, where a palm-leaf manuscript emerges from darkness, and the research
  drawer's evidence motif. A hero close-up asset.
- **Scale.** 0.5 m long × 0.07 m wide × 0.06 m thick when closed. Origin at the bottom-centre of the stack.
  The long axis lies along X, and the top cover faces +Y.
- **Geometry.**
  - Painted wooden covers top and bottom, as nodes `cover_top` and `cover_bottom`, with lotus and
    vine motifs (texture).
  - 24 individual leaves, `leaf_00` … `leaf_23`, each pivoted along its left spine edge so the engine can
    fan or turn them.
  - Two string holes and a cord (separate node `cord`) with a bone toggle.
  - An "open" state can be posed procedurally by rotating the leaves.
- **Materials.** Aged palm leaf (albedo `#D9C39A` with darker edges, roughness 0.7, incised script-like
  marks as **illegible decorative strokes**, never real text), lacquered red and gold covers, and cotton cord.
- **Budget.** ≤ 8 k tris, 2 materials (leaves, covers) at 1024².
- **Prompt.**
  ```
  An ancient Indian palm-leaf manuscript bundle, stylised, stack of long narrow aged palm leaves with
  darkened edges and faint incised decorative script-like marks (illegible), painted wooden covers with
  red, gold and lotus-vine motifs, two cord holes with a cotton cord and bone toggle, reverent close-up
  hero prop, 0.5 m long, game-ready PBR
  ```

### 7.6 `obj.dice`: the dice set (FULL)
- **Use.** The dice game. In the epic the game is called *dyuta* and the dice *aksha*. Early dice are
  widely understood as vibhitaka nuts, and later depictions show oblong four-sided dice (pasa). The asset
  offers both, honestly, as separate nodes.
- **Scale.** The set sits on a 0.6 × 0.6 m embroidered game cloth (node `cloth`). There are 3 oblong dice
  (`die_0` … `die_2`), each 0.07 × 0.014 × 0.014 m, with pips on four long faces. A small heap of about 12
  vibhitaka nuts (`nuts`, ~0.02 m each). Origin at the cloth centre, y = 0.
- **Materials.** Ivory or bone dice (roughness 0.35) with inlaid dark pips, nuts (brown, roughness 0.8),
  and a crimson and gold embroidered cloth.
- **Budget.** ≤ 4 k tris, 1–2 materials. Each die is its own node, pivoted at its centre, for the `roll` animation.
- **Prompt.**
  ```
  An ancient Indian dice game set, stylised, three long four-sided ivory dice with inlaid dark pips,
  a small heap of brown vibhitaka nuts, on a square crimson cloth embroidered with gold geometric
  borders, ominous still-life, game-ready PBR prop, 0.6 m cloth
  ```

### 7.7 `obj.throne`: the royal throne, simhasana (FULL)
- **Use.** The throne room dais (`throne_mount`), and the coronation scenes.
- **Scale.** 1.6 m wide × 2.4 m tall (back finial) × 1.3 m deep. Seat height 0.55 m above its own base.
  Origin at bottom-centre. The seat faces +Z, out from the dais toward the hall.
- **Geometry.** A lion throne: two seated lion supports as front legs, a raised seat with a cushion, a
  tall back with a sun-disc halo and lotus petals, armrests ending in makara heads, a footstool (separate
  node `footstool`), and a parasol mount empty above (`chhatra_mount`).
- **Materials.** Gilded wood (gold metalness 1, roughness 0.3), crimson velvet cushion (roughness 0.85),
  and inset red and green stone cabochons (roughness 0.15).
- **Budget.** ≤ 12 k tris, 2 materials.
- **Prompt.**
  ```
  An ancient Indian royal lion throne, stylised mythological, two carved seated lions as front supports,
  tall back with a radiant sun-disc halo and lotus petals, armrests ending in makara heads, crimson
  velvet cushion, gilded wood with red and green stone inlays, matching footstool, majestic, game-ready
  PBR prop, 2.4 m tall
  ```

### 7.8 `obj.banner`: royal banners and standards (FULL)
- **Use.** The generic war and city standards. In the epic, the heroes' banners carry emblems:
  Bhishma's palm tree, Drona's altar and water-pot, Duryodhana's serpent, Karna's elephant-rope, and so
  on (details vary). Support emblem swaps via **texture variants**; the mesh stays the same.
- **Scale.** Pole 6 m tall. Flag 1.6 m (hoist) × 2.4 m (fly), tapering to a swallowtail. Origin at the
  pole base. The flag extends toward +X.
- **Geometry.**
  - A pole with a gilded finial and a bronze ferrule.
  - The flag as `flag_cloth`, a subdivided plane of 16 × 12 quads so the engine's `flutter` can deform it.
    The flag uses a pinned-edge gradient in its vertex colour R channel (1 = free, 0 = pinned) so the
    shader knows which edge is fixed.
  - Optionally a small bell cluster at the finial.
- **Materials.** Dyed cotton (roughness 0.9, double-sided), gold, wood. Emblem decals come from a separate
  1024² texture per hero.
- **Budget.** ≤ 2 k tris, 2 materials. `instancing: true` is possible for armies (one material, emblem atlas).
- **Prompt.**
  ```
  An ancient Indian war standard, stylised, tall wooden pole with a gilded finial and small bells,
  long swallowtail cloth banner in rich dyed colours with a central emblem, gently waving, game-ready
  PBR prop, 6 m tall
  ```

### 7.9 `obj.kapidhvaja_banner`: Arjuna's Kapidhvaja, the monkey banner (FULL)
- **Use.** Arjuna's ensign. The epic describes a monkey on his banner, traditionally identified with
  Hanuman. It mounts on `obj.krishna_chariot`'s `banner_mount`.
- **Scale.** Pole section above the chariot 3.0 m. Flag 1.4 × 2.0 m. Origin at the pole base (which
  meets `banner_mount`).
- **Geometry.**
  - A pole with a gilded finial.
  - `flag_cloth`, a subdivided plane as in `obj.banner`.
  - An *original*, stylised emblem of a leaping monkey figure holding a mountain-top or mace. Make it
    heraldic and flat, and do not copy any existing artwork.
  - Optionally a small three-dimensional gilt monkey finial (node `finial`) instead of a spear-point.
- **Materials.** Saffron-gold cloth `#D98A2B` with a deep red emblem `#7A1F1A` and gold outline, plus a gilded finial.
- **Budget.** ≤ 3 k tris, 2 materials.
- **Prompt.**
  ```
  A sacred chariot banner from the Indian epic, stylised, saffron-gold swallowtail flag bearing an
  original heraldic emblem of a leaping divine monkey in deep red with gold outline, tall pole with a
  small gilded monkey finial, fluttering, game-ready PBR prop
  ```

### 7.10 `obj.bed_of_arrows`: Bhishma's bed of arrows (FULL)
- **Use.** Bhishma, fallen on day ten, rests on a bed of arrows until the winter solstice and teaches
  Yudhishthira. The tableau must be serene, reverent and free of gore. The figure (`char.bhishma`, pose
  `lying`) is placed on top; the asset is the bed alone.
- **Scale.** 2.4 m long × 0.9 m wide. The body rest plane sits 0.9 m above the ground, with a head rest at
  1.05 m. Origin at bottom-centre. The head end is toward −X.
- **Geometry.**
  - About 120 arrows planted in the earth at slight angles, forming a cradle whose fletched ends meet at
    the body plane.
  - A pillow of three arrows at the head end (the epic tells that Arjuna provided it).
  - Scattered earth and a few fallen petals as a decal-like mesh.
- **Construction.** Build one master arrow, ≤ 60 tris, and array it in Blender, then join everything into
  one mesh for the export.
- **Materials.** Reed shafts `#B89A6A`, grey-white fletching, bronze heads (half buried), dark earth. One material via an atlas.
- **Budget.** ≤ 10 k tris, 1 material.
- **Prompt.**
  ```
  A bed made of many arrows planted in the earth at slight angles forming a cradle about knee height,
  fletched ends meeting in a level resting surface, a small pillow of three arrows at the head end, dark
  battlefield earth with a few fallen petals, solemn and serene, no blood, no body, game-ready PBR, 2.4 m long
  ```

### 7.11 Other object slots (compact briefs)

| Slot | Brief (scale · origin · materials · budget) |
|---|---|
| `obj.chariot_generic` | A war chariot like 7.1 without horses and with plainer timber: 3.0 m long, `wheel_L`/`wheel_R` pivots, a `banner_mount`. ≤ 15 k tris. Instancing possible for armies (one material). |
| `obj.horse` | A single horse, 2.4 m long, 1.65 m at the withers, faces +Z, pivot between the hooves. White or bay via registry tint. ≤ 8 k tris, one material, instancing true. |
| `obj.crown` | A royal crown (mukuta), 0.28 m tall, origin at the base ring centre. Gold with stones. ≤ 3 k tris. |
| `obj.palm_leaf` | A single palm leaf, 0.45 × 0.05 m, origin at the left edge centre (pivot for turning). Decorative illegible strokes. ≤ 200 tris, instancing true. |
| `obj.divine_weapon` | A generic celestial astra: a glowing spear or arrow form 1.4 m long, origin at the centre, emissive gold-white core. ≤ 2 k tris. Never aimed at a figure in staging. |
| `obj.shield` | Round shield 0.7 m: hide and bronze boss, painted rim. Origin at the grip. ≤ 1.5 k tris, instancing true. |
| `obj.arrows` | A quiver of arrows 0.8 m, origin at the strap point. Reed, fletching, leather. ≤ 1.5 k tris, instancing true. |
| `obj.war_drum` | A large war drum (bheri or dundubhi style), 1.2 m in diameter on a stand, origin at bottom-centre, hide head, bronze rim. The skin is a separate node `drumhead` for pulse. ≤ 4 k tris. |
| `obj.palace_door` | A double teak door 3 × 5 m with bronze studs and a carved frame. `door_L`/`door_R` hinge pivots. Origin at the threshold centre. ≤ 4 k tris. |
| `obj.lamp` | A bronze oil lamp (diya on a stand), 0.6 m, origin at the base, with a `flame` empty at the wick for `env.fire`. ≤ 800 tris, instancing true. |
| `obj.sacred_tree` | A banyan or peepal tree 14 m tall with aerial roots, origin at the trunk base, foliage as clustered cards (alpha-tested) or sculpted clumps. ≤ 25 k tris, 2 materials. |
| `obj.fish_target` | The swayamvara target: a rotating wheel 1.2 m in diameter with a small wooden fish on top, mounted on a pole. The wheel node `wheel` rotates about Y. ≤ 2 k tris. |
| `obj.mace` | A gada 1.3 m long, origin at the grip, with a spherical fluted bronze head and gilded bands. ≤ 1.5 k tris. |
| `obj.conch_generic` | A plain conch, 0.25 m, like 7.4 without the filigree. ≤ 2 k tris. |
| `obj.water_vessel` | A clay or copper water pot (kalasha), 0.35 m, origin at the base. ≤ 1 k tris, instancing true. |
| `obj.funeral_pyre` | A stacked-log pyre, 2.4 × 1.2 × 1.0 m, draped in white cloth, garlands, no body visible. A `fire_mount` empty for `env.fire`. Solemn. ≤ 5 k tris. |
| `obj.dog` | The dog that follows Yudhishthira on the final journey (Dharma in disguise, as the epic tells): a lean, noble pariah-type dog, 0.75 m long, faces +Z, pivot between the paws. ≤ 4 k tris. |

---

## 8. Environment

Many environment slots are better left **procedural or shader-driven**, and the factories already do this
well: skies, stars, mist, dust, rain, snow, fire, clouds, the cosmic mandala and water surfaces. A GLB is
recommended only for the land masses and the distant massing listed in §8.1–§8.3 and the table. The others
are briefed so that any future mesh stays consistent.

### 8.1 `env.battlefield_terrain`: the Kurukshetra battlefield environment (FULL)
- **Use.** Kurukshetra and the per-commander battlefields (`bhishma_battlefield`, `drona_battlefield`,
  `karna_battlefield`): a vast, sacred, dusty plain. The epic calls it *dharmakshetra*. It is
  traditionally identified with the Kurukshetra region of present-day Haryana; the voyage shows it "as
  described in the epic", with no attempt at mapping.
- **Scale.** 400 × 400 m terrain (±200 m) with gentle undulation (±3 m) and the central 30 m area kept
  near-flat for the stage disc. A low ridge on the north horizon (z −180…−200, rising 12 m) frames the
  camera. Origin at the centre, y = 0.
- **Geometry.**
  - A heightfield mesh, denser near the centre and coarser outward.
  - Chariot ruts and hoof-churned earth as normal and height detail.
  - Sparse dry grass patches as vertex-colour masks, with no geometry; grass cards are instanced by the
    engine if wanted.
  - A few scattered stones as separate instanced meshes `rock_0…3`.
  - A dry riverbed channel along one edge.
  - **No** broken bodies, weapons in bodies or gore.
- **Materials.** A tiling dusty ochre earth (1024², roughness 0.95) blended by vertex colour with darker
  trampled soil and pale dry grass. A macro albedo variation texture at 512².
- **Palette.** Dust ochre `#B08A5A`, trampled earth `#6B4B33`, dry grass `#A89A6A`, sky-matched fog from the location palette.
- **Budget.** Terrain tier: ≤ 80 k tris, 2 materials. Rocks ≤ 600 tris each, instancing true.
- **Companion slots.** `env.army_formation` (§8 table) and `env.dust`.
- **Prompt.**
  ```
  A vast ancient battlefield plain from the Indian epic, stylised, dusty ochre earth with gentle
  undulation, chariot ruts and hoof-churned soil, sparse patches of pale dry grass, a dry riverbed along
  one edge, low ridge on the horizon, scattered weathered stones, solemn and sacred, no bodies, no
  weapons, no blood, game-ready terrain, 400 m by 400 m
  ```

### 8.2 `env.himalaya_peaks`: the Himalayan path, Mahaprasthana (FULL)
- **Use.** The final journey: the Pandavas and Draupadi walking north into the Himalaya, one falling
  after another, Yudhishthira continuing with the dog. The scene is austere, luminous and quiet.
- **Scale.** A 400 m wide backdrop of peaks rising to 140 m at z −150…−200. A walkable path ribbon 3 m
  wide winds from the stage disc (origin) up toward −Z on a gentle 8–12° grade, with switchbacks.
  Origin at the path start, y = 0.
- **Geometry.**
  - Path ribbon (node `path`), with snow-dusted rock shoulders either side.
  - Peaks as layered silhouettes (nodes `peaks_near`, `peaks_far`) so the fog can separate them.
  - A few prayer-stone cairns along the path (no modern prayer flags; keep it epic-age and neutral).
  - Snowfields as smooth meshes.
- **Materials.** Granite (roughness 0.85), snow (roughness 0.5, slight blue tint `#E8EEF6`) and an ice
  highlight. The far peaks carry a baked atmospheric gradient so they stay legible at low quality.
- **Budget.** Terrain tier: ≤ 70 k tris, 3 materials.
- **Companion slots.** `env.snow`, `env.mist`, `env.road` (the path may be split out as `env.road`).
- **Prompt.**
  ```
  A high Himalayan mountain path from the Indian epic, stylised, narrow winding stone path climbing
  gently between snow-dusted granite shoulders, small stone cairns along the way, layered majestic
  snow peaks behind fading into atmospheric haze, austere luminous dawn mood, quiet and sacred,
  game-ready terrain, 400 m wide backdrop
  ```

### 8.3 Other environment slots (compact briefs)

| Slot | Recommendation & brief |
|---|---|
| `env.mountains` | A GLB is useful here. Generic distant ranges: 600 m wide silhouette band, 60–120 m tall, layered `near`/`far` nodes, baked haze gradient. ≤ 20 k tris. |
| `env.river` | Riverbanks mesh as a GLB (sandbanks, reeds as instanced cards, ghats steps optional) 200 m long, 30 m wide channel. Keep the water surface procedural (`env.ganga_water`/`env.water`). ≤ 30 k tris. |
| `env.ganga_water` | Keep procedural (shader water). If meshed: a flat 400 × 60 m subdivided plane, UV-flow-mapped, material only. |
| `env.forest` | Instanced tree kit: 3 tree meshes (sal, banyan, ashoka-like), each ≤ 3 k tris, one atlas material, instancing true, origin at the trunk base. |
| `env.sacred_grove` | A ring of 7 old trees with a stone platform, offerings, lamp empties. ≤ 25 k tris. |
| `env.road` | A path or road ribbon module 20 m long, 4 m wide, origin at one end so modules chain. Packed earth with cart ruts. ≤ 2 k tris. |
| `env.city_skyline` | Distant city massing card-mesh 300 m wide, towers and domes, low detail with baked lighting. ≤ 15 k tris. |
| `env.palace_garden` | Formal garden 40 × 40 m: water channels, lotus pools, clipped shrubs (instanced), pavilions as empties. ≤ 30 k tris. |
| `env.army_formation` | Keep procedural (`InstancedMesh` of `char.soldier_generic`, `obj.chariot_generic`, `obj.horse`, `obj.banner`). Any GLB here would be a single low-poly "massed block" impostor ≤ 5 k tris for far LOD. |
| `env.clouds` | Keep procedural. If meshed: 6 soft cloud puff meshes, ≤ 1 k tris each, vertex-colour shaded, no textures. |
| `env.mist` | Keep procedural (fog and particles). |
| `env.dust` | Keep procedural (particles). |
| `env.rain` | Keep procedural (particles and shader streaks). |
| `env.fire` | Keep procedural (flicker shader and embers). If meshed: 3 crossed flame cards with emissive gradient, ≤ 100 tris. |
| `env.stars` | Keep procedural (points). |
| `env.sky_dawn` / `env.sky_day` / `env.sky_dusk` / `env.sky_night` | Keep procedural (gradient sky dome driven by `location.palette`). An optional painted equirectangular 4096 × 2048 KTX2 is allowed only if it is original artwork. |
| `env.cosmic_mandala` | Keep procedural (the opening sequence's golden mandala is generated). If meshed: a flat 12 m radial mandala with 8–16-fold symmetry, emissive gold lines, ≤ 10 k tris, layered rings as separate nodes for staggered rotation. |
| `env.snow` | Snow-covered ground patches (smooth meshes, ≤ 5 k tris). Falling snow stays procedural. |
| `env.lake` | Lakeshore mesh with lotus pads (instanced) and stone steps, 80 m across. The water surface stays procedural. Used by the Yaksha Prashna scene. ≤ 20 k tris. |

---

## 9. Slot coverage index

Every canonical asset id (DATA_CONTRACT §1) has a brief in this document:

- **Architecture (20).** Full: hastinapura_palace, indraprastha_palace, maya_sabha, dice_hall,
  throne_room, gurukul, hermitage, city_gate. Compact: pillared_corridor, sabha_hall, royal_courtyard,
  temple, battle_camp, chariot_shelter, royal_balcony, lac_house, swayamvara_arena, dwaraka_city,
  yajna_altar, city_walls.
- **Characters (50 named + 5 generic).** Full: krishna, arjuna, bhima, yudhishthira, nakula, sahadeva,
  draupadi, bhishma, drona, karna, duryodhana, shakuni, kunti, gandhari, abhimanyu, vidura. Compact: all
  remaining `char.*`, including sage_generic, soldier_generic, courtier_generic, archer_generic and yaksha.
- **Objects (27).** Full: krishna_chariot, gandiva, sudarshana, panchajanya, manuscript, dice, throne,
  banner, kapidhvaja_banner, bed_of_arrows. Compact: chariot_generic, horse, crown, palm_leaf,
  divine_weapon, shield, arrows, war_drum, palace_door, lamp, sacred_tree, fish_target, mace,
  conch_generic, water_vessel, funeral_pyre, dog.
- **Environment (24).** Full: battlefield_terrain (Kurukshetra), himalaya_peaks (Himalayan path).
  Compact: mountains, river, ganga_water, forest, sacred_grove, road, city_skyline, palace_garden,
  army_formation, clouds, mist, dust, rain, fire, stars, sky_dawn, sky_day, sky_dusk, sky_night,
  cosmic_mandala, snow, lake.

To add a new asset family member, first add the id to DATA_CONTRACT §1 and to the schema's asset id list,
then add a registry entry with `glb: null` and a working `fallback`, then add a brief here. Only after all
three, start the §3 workflow.
