# Demo video: shot list and recording notes

The assignment asks for **five minutes or less** showing **a full run on your demo dataset**
and **a walk through the report**. That is the whole requirement — there is no mandated
structure beyond those two things.

## Before you record

1. `data/demo.jsonl` regenerated with `python tools/make_demo.py` so every Indian-language
   string comes from Indic-Translate.
2. `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` set, so the judge tier actually runs.
3. One clean run already done, so `./report` exists and the numbers are real.

```bash
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report --generate
```

## Shot list (≈4:30)

| Time | Show | Say / point at |
| --- | --- | --- |
| 0:00 | Terminal, clean directory | "This is the recipe from the Cookbook PR. One command installs it." |
| 0:20 | `pip install -r requirements.txt` | pinned dependencies, no system deps |
| 0:40 | `pytest -q` → 46 passed | tests run with no network |
| 1:00 | The run command with `--generate` | explain `--generate` fills translations and audio |
| 1:30 | Run output scrolling | point at cost line and the digest at the end |
| 2:00 | `report/summary.md` | headline table: recall, false alarms, localization |
| 2:30 | Per-category table | "mistranslation recall is low — that is the judge's job" |
| 3:00 | `report/items.jsonl`, one flagged item | the span, the category, the evidence string |
| 3:30 | Re-run with `--baseline` | open `diff.md`: what improved, what regressed |
| 4:00 | Re-run again, unchanged | "cached rerun: ₹0.00 and the same digest" |
| 4:20 | Close | limitations, and what you'd build next |

## Two ways to record it

**Terminal segment — `charmbracelet/vhs`.** Declarative `.tape` file, no human input,
pixel-perfect output. A starter is in `tools/demo.tape`:

```bash
vhs tools/demo.tape        # produces demo.mp4
```

**Report and page segment — `microsoft/playwright`.** `tools/record_demo.py` records the
project page and the report at 1920×1080. See that file's docstring.

**`remotion` is not worth it here** — it is a React render pipeline for designed motion
graphics; a screen recording of real output is what the brief asks for.

**`asciinema/agg`** is fine if you already use asciinema, but it needs asciinema installed
to capture first, and it only covers the terminal half.

## Hosting

Upload to YouTube (unlisted) or Google Drive with link sharing, then put the link in the
README and the PR body. It must open without signing in.
