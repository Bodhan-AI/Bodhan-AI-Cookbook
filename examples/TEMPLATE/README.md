# TEMPLATE

> <one-line description of what this recipe does and for whom>

**Bodhan models:** <!-- e.g. indic-transcribe, indic-translate -->
**Languages shown:** <!-- e.g. Hindi, Tamil -->
**Format:** <!-- notebook / FastAPI app / CLI / Next.js app -->

## What it does

<!-- 2–4 sentences. What goes in, what comes out, why someone would want it. A screenshot or a short sample output is worth a lot here. -->

## Setup

```bash
cd examples/TEMPLATE
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your key from https://console.bodhan.ai
```

## Run

```bash
jupyter notebook TEMPLATE.ipynb
# or: python app.py
```

## How it works

<!-- Optional. One line per pipeline step, e.g.
1. Transcribe the audio with `indic-transcribe`
2. Translate the transcript with `indic-translate`
3. Speak the translation with `indic-speak`
-->

## Limitations

<!-- Optional but honest: audio length limits, languages not covered, cost notes. -->

---

*Starting a new recipe? Copy this folder (`make new-recipe NAME=my_recipe`), rename `TEMPLATE.ipynb` to match the folder, fill in every section above, delete the HTML comments, then run `python scripts/validate_recipe.py examples/my_recipe`. See [CONTRIBUTING.md](../../CONTRIBUTING.md).*
