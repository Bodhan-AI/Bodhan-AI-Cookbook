# Six-case execution evidence

These are original synthetic fixtures and real model responses. Automated checks
are mechanical screens, not fluent-language or hidden-task certification.

[3:32 recording](https://github.com/BishalJena/Bodhan-AI-Cookbook/releases/download/tool-agent-demo-20261117/demo_recording.mp4) · [Full evidence bundle](https://github.com/BishalJena/Bodhan-AI-Cookbook/releases/tag/tool-agent-demo-20261117)

| Case | Final state | Evidence and caveats |
| --- | --- | --- |
| Tamil chapter | completed | 8 scanned pages; 90.88s MP3 + Tamil text. New school-garden input; old water-cycle partial remains historical. |
| Marathi worksheet | completed | 4 rows x 4 cells, values 18/24/30 preserved. Small-table batching follows a retained partial attempt. |
| Hindi subtitles | completed replay | Eight SRT windows cover 0-180s. Final replay is enforced cache-only; real ASR/translation and recovery runs remain retained. Artificial synthetic loop. |
| Telugu selection | completed | Clips 1 and 4 selected from script evidence; ambiguous clips left unknown. Clip 4 duplicates clip 1 intentionally. |
| Bengali narration | completed correction | 8.96s fixed female Ishita voice + text. Earlier local partial and single context-checked budget carry retained. |
| French video | failed/refused as expected | Clear unsupported-language explanation; no fabricated French output. |

## Runtime and cost

| Final run | Seconds | New accounting INR | Retained-attempt context |
| --- | ---: | ---: | --- |
| chapter02 | 142.299 | 0.871667 | Case accounting INR 3.756152; Real autonomous CLI run; successful model cache entries may be reused. |
| worksheet02 | 12.093 | 0.009460 | Case accounting INR 0.211740; Real autonomous CLI run; successful model cache entries may be reused. |
| subtitles04 | 7.61 | 0.000000 | Case accounting INR 0.388400; Enforced cache-only final replay of retained real calls. |
| mixed01 | 55.624 | 0.042367 | Case accounting INR 0.042367; Real autonomous CLI run; successful model cache entries may be reused. |
| bengali | 107.192 | 0.126853 | Case accounting INR 0.126853; Earlier local real correction; cumulative cost already included in baseline, not added twice. |
| french02 | 6.464 | 0.000000 | Case accounting INR 0.000000; Real autonomous CLI run; successful model cache entries may be reused. |

The release bundle contains source media, outputs, original reports/traces, captures,
hashes, fixture records and partial/failed attempts. It excludes `.env`, API keys,
private cache, continuation keys and unrelated files.

The 3:32 video is actual subprocess/trace playback rendered after capture, with
reading pauses and condensed waits. It is not uninterrupted desktop capture.
A real Bengali excerpt is included and labeled AI-generated.

Source documents are regenerated offline with:

```bash
uv run python scripts/make_final_inputs.py --out outputs/new_inputs
```

`scripts/prepare_final_audio.py` uses Bodhan and requires an explicit cap. Retained
offline assembly follows a provider 403: one verified Telugu clip is duplicated and
verified Bengali audio is reused. Labels stay outside the model-facing input folder.
All preparation costs count. The Hindi input is a synthetic 180-second loop.

The final chapter took 142.299s; its earlier partial took 184.067s. Combined
development attempts exceed the five-minute quality target. No first-attempt or
production-latency guarantee is implied. Fluent and candidate review are pending.
