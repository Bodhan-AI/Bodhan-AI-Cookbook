## Summary

An agentic pipeline that dubs an English video into Indian languages with
Bodhan's Indic models. One command produces, per language, a dubbed MP4 with an
embedded subtitle track, an SRT file, and a machine-readable report covering
sync, quality, retries, runtime and cost. The interesting part is not the model
calls — it is keeping translated speech inside the original timing while the
source audio's non-speech content survives.

**Type:** new recipe
**Path:** `examples/video_dubbing/`
**Bodhan models used:** `indic-transcribe`, `indic-translate`, `indic-speak`

## Checklist

- [x] I started from `examples/TEMPLATE/` (new recipes) and the folder matches the required layout
- [x] `README.md` explains what the recipe does, how to set it up, and how to run it
- [x] Dependencies are pinned (`name==version`) in `requirements.txt`
- [x] `.env.example` lists every variable; **no real key anywhere in the diff**
- [x] Notebook outputs are cleared (`make clean-outputs`) — this recipe has no notebook
- [x] Only current model ids from `scripts/bodhan_api_rules.json` are used
- [ ] `make check` passes locally — **not yet**; it fails on a pre-existing failure in
      `examples/AndroidSpeak` that reproduces on a clean `origin/main`. See
      [Local gate](#local-gate) below for the evidence and for the checks that do pass.
- [x] I ran the recipe end to end against `https://api.bodhan.ai` with my own key

## Screenshots / sample output

Demo lesson: *Class 9 Mathematics, Chapter 6, Period 1* — 5 min 29 s, English,
72 speech segments.

| | Hindi | Tamil | Bengali |
|---|---|---|---|
| Segments dubbed | 72 / 72 | 72 / 72 | 72 / 72 |
| Mean back-transcription CER | 0.071 | 0.140 | 0.078 |
| Loudness | −16.07 LUFS | −16.05 LUFS | −16.26 LUFS |
| Duration drift | +0 ms | +0 ms | +0 ms |
| Cost | ₹1.97 | ₹2.65 | ₹2.37 |

Outputs — each folder holds the dubbed MP4, the SRT, and that run's `report.json`:

- [Hindi](https://drive.google.com/drive/folders/1sucwTCWTJDFbhbQqlrCEPOMs8YebcqHN?usp=drive_link)
- [Tamil](https://drive.google.com/drive/folders/1JXYUltfA66PJpch0VXN5lMpGwXnfx4is?usp=drive_link)
- [Bengali](https://drive.google.com/drive/folders/1zAUiLU39sESAJeR8g01E5Sc3Q3q4BwNY?usp=drive_link)

Two segments from `segments.hi.json`, showing the per-segment record:

```
id=2  7.32-11.24  tempo=1.00  fit=ok  cer=0.0
EN: From class seven you already know four kinds of angle
HI: कक्षा सात से ही आप चार प्रकार के कोणों को जानते हैं।

id=3  11.34-17.44  tempo=1.00  fit=ok  cer=0.0
EN: An acute angle is less than ninety degrees A right angle is exactly ninety degrees
HI: एक न्यून कोण नब्बे डिग्री से कम होता है। एक समकोण ठीक नब्बे डिग्री होता है।
```

Demo walkthrough (under five minutes — a live run, then the outputs and report):
https://drive.google.com/file/d/14OU2wHgdFJ0-tl7k50DfzSCboop537Tl/view?usp=drive_link

## Additional detail

The assignment asked for architecture, workflow, LLM usage, results, demo,
trade-offs and cost in the PR as well. All of it is written up inside the recipe:

| Document | Contents |
|---|---|
| `docs/architecture.md` | Component diagram, one line per module, data flow |
| `docs/workflow.md` | Flow chart with retries, checks and stop conditions |
| `docs/design.md` | Where the LLM decides, how sync and quality are checked, trade-offs |
| `docs/plan.md` | Build plan, and what each live run proved |

**Pipeline in one line:** VAD finds speech → chunks capped at 28 s and cut at the
quietest frame → `indic-transcribe` per chunk, whose boundaries *become* the
segment timestamps (the hosted ASR returns text only) → consecutive segments are
translated as a block with one segment per line, so the reply comes back aligned
→ `indic-speak` per segment → every clip is back-transcribed and scored →
`atempo` ≤1.20 and a silence trim fit it to its slot → the original audio is
gated and mixed under the dub → `loudnorm` to −16 LUFS → muxed with `-c:v copy`
and an embedded `mov_text` subtitle track.

**Where the LLM is used:** exactly one decision — shortening *English* when a
segment cannot fit its slot at 1.20×, after which `indic-translate` produces the
Indic text. Its answer is rejected if it contains any non-Latin character, drops
a number, or drops a glossary term. Without `LLM_*` variables the step is skipped
and the ladder ends at a logged miss.

**Known limitations:** one consistent voice per language (no diarization); music
under speech is removed with the speech (source separation is the upgrade path);
subtitles are an embedded track, so QuickTime will not show them — VLC does, and
`--burn-subtitles` exists for players that ignore tracks.

### Local gate

`make check` fails on this repository at `examples/AndroidSpeak`, a recipe this
PR does not touch. It reproduces on a clean checkout of `origin/main`:

```
[ERROR] examples/AndroidSpeak: no notebook or source file found (.ipynb/.py/.js/.ts)
[ERROR] examples/AndroidSpeak: .env.example is missing
[ERROR] examples/AndroidSpeak/.gitignore: .gitignore does not ignore .env
```

AndroidSpeak is a Kotlin/Android app; the checker only recognises
`.py/.ipynb/.js/.ts/.tsx` as source files, so it cannot pass. I left it alone
because fixing it means editing shared tooling or another author's recipe.

Everything that applies to this PR passes:

```
python scripts/validate_recipe.py examples/video_dubbing   -> OK — no findings
python scripts/bodhan_rules.py --check                     -> OK
ruff check scripts/ tests/                                 -> All checks passed
pytest tests/ -k "not all_shipped_recipes_pass"            -> 31 passed
pytest examples/video_dubbing/tests -q                     -> 259 passed, 1 skipped
```

## AI assistance

Built with an AI coding assistant, used for scaffolding, test generation and
review. Every design decision was made against measured behaviour rather than
generated: the VAD-derived timestamps, the line-aligned translation, the adaptive
concurrency cap and the two-pass loudness step were each changed after a real run
showed the previous approach was wrong. Those reversals are recorded in
`docs/plan.md` under "What the live run proved", including the mistaken 450 ms VAD
bridge and the 60-second cooldown that turned a three-minute job into fourteen.
