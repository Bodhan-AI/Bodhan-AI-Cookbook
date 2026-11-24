# Dashboard — "the proofreader's desk"

A single, self-contained `report/dashboard.html`: inline CSS + JS + JSON data, **no server, no
CDN, no network**. It opens by double-click and works offline. Generated automatically after every
run (unless `--no-dashboard`), or on demand:

```bash
python -m bodhan_eval.dashboard --report ./report
# or open report/dashboard.html directly
```

## What it shows
- **Header strip** — item counts (clean / flagged / uncertain / skipped), languages, logical cost.
- **KPI gauges** — recall, false-alarm rate, localization, category accuracy, judge consistency,
  each with its target marked as a tick; green ink if met, red if missed.
- **Heatmap** — language × category recall (greener = higher), precision + n on hover, dashed for
  low-support cells; click a cell to filter.
- **Filter bar** — language, category, severity, status, free-text search, and a "judge ≠ checks"
  toggle. Filters are mirrored in the URL hash so a view can be shared. Reset button + live count.
- **Item list** — compact rows with id, language chip, status, category/severity flag chips, cost.
- **Proof sheet** (click a row, or `j`/`k` to move, `Esc` to close) — source and translation side
  by side with the **flagged spans highlighted** (highlighter for minor/major, wavy red underline
  for critical) and numbered margin markers linked to evidence cards; the audio-energy strip is
  drawn from precomputed RMS (no audio embedded); a one-line "why flagged / why not" explains the
  combiner decision, with the judge verdict and any disagreement.
- **Tabs** — Calibration (precision/recall/F1/localization/category-accuracy per cell, false-alarm
  table), Cost (logical spend, per-100-item extrapolation, budget skips), Review queue (uncertain
  items with a client-side CSV export of the current selection), and Diff (regressions first).

## Design & accessibility
Warm paper theme with a **midnight** dark theme (follows `prefers-color-scheme`, toggle with the
header button or `t`). Serif headings, Indic font stacks with per-element `lang` attributes and
generous line-height for Devanagari/Tamil/Bengali. Severity is never colour-only — it pairs an ink
colour with an icon dot and a text label. Semantic HTML, keyboard navigation (`j`/`k`/`Esc`/`/`/`t`/`?`),
ARIA labels, a print stylesheet, and `prefers-reduced-motion` are all respected. **All item text is
escaped** (Markdown/LaTeX shown as plain text); there is no `eval` and no `innerHTML` with
untrusted strings, and `</` in the embedded data is neutralized so no string can break out of the
data block.

## Screenshots
_Add two screenshots here from `sample_report/dashboard.html` (open it and capture the item list +
a proof sheet with a highlighted number error — the best 10 seconds of the demo video)._
