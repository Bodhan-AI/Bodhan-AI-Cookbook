# Contributing to the Bodhan AI Cookbook

Thank you for helping developers build with Indian-language AI. This guide covers everything you need to add a recipe, fix one, or improve the tooling. It is short on purpose; the CI gate enforces the rest.

## Contents

- [Ways to contribute](#ways-to-contribute)
- [Before you start](#before-you-start)
- [Adding a recipe](#adding-a-recipe)
- [Recipe layout](#recipe-layout)
- [Security requirements](#security-requirements)
- [API standards](#api-standards)
- [Local validation](#local-validation)
- [Pull request process](#pull-request-process)
- [Reporting bugs](#reporting-bugs)
- [Code of conduct](#code-of-conduct)

## Ways to contribute

| Contribution | Where it goes |
|---|---|
| A complete app or end-to-end project | `examples/<snake_case_name>/` |
| A focused tutorial on one API | `getting-started/<capability>/` |
| A guide for using Bodhan with a framework or platform | `integrations/<framework_name>/` |
| A fix to an existing recipe | in place, with a note in the PR |
| Better validation tooling or docs | `scripts/`, `tests/`, top-level docs |

Not sure whether an idea fits? Open a [recipe proposal](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues/new?template=recipe_proposal.md) first and a maintainer will confirm the scope.

## Before you start

1. Get an API key from [console.bodhan.ai](https://console.bodhan.ai) and read the [API reference](https://console.bodhan.ai/api-docs/).
2. Search [existing issues](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues) and [open pull requests](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pulls) so you don't duplicate work.
3. Fork the repository and create a branch: `git checkout -b recipe/<snake_case_name>`.

## Adding a recipe

The fastest path:

```bash
make new-recipe NAME=my_new_recipe        # copies examples/TEMPLATE
# ... build it ...
python scripts/validate_recipe.py examples/my_new_recipe
```

Or copy `examples/TEMPLATE/` by hand. Either way, every recipe needs a working README, pinned dependencies, a `.env.example`, and a run-through against the real API before you open the PR.

**Notebook or app?** Both are welcome. A Jupyter notebook is the best format for teaching an API step by step. A FastAPI, Streamlit, Next.js or CLI app is better when the point is the product. If you ship an app, keep a short walkthrough in the README so readers can follow along without running it.

**Companion models.** Bodhan currently offers speech, translation, transliteration and OCR models. If your recipe also needs a general-purpose LLM (for summarising, chat, reasoning), bring one of your choice, read its key from a separate environment variable, and document it in `.env.example`. Bodhan calls must still go through `https://api.bodhan.ai`.

## Recipe layout

```
examples/my_new_recipe/
├── README.md              # what it does, setup, run, how it works
├── my_new_recipe.ipynb    # or app.py / src/ — the code
├── requirements.txt       # pinned: name==version
├── .env.example           # every variable, placeholder values only
├── .gitignore             # must ignore .env
├── sample_data/           # small inputs (< 1 MB each); .gitkeep if empty
└── outputs/               # generated files, git-ignored; .gitkeep
```

README sections the validator looks for: `## Setup` and `## Run`. Add `## How it works` when the pipeline has more than one step.

Naming: directories and notebooks are `snake_case` and match each other. Keep names descriptive of what the recipe does, not the model it uses (`exam_paper_reader`, not `ocr_demo`).

Getting-started tutorials and integration guides are single notebooks and are exempt from the `.env.example` / `.gitignore` rule. They read the key from `BODHAN_API_KEY` in the environment.

## Security requirements

These are blocking. CI fails the PR on any of them.

- **Never commit a real API key.** Read it from the environment: `os.environ["BODHAN_API_KEY"]` in Python, `process.env.BODHAN_API_KEY` in Node.
- **Never commit `.env` files.** Commit `.env.example` with placeholders instead.
- **Never expose a key in client-side code.** Browser and mobile recipes must call Bodhan through a small backend or serverless function.
- **Clear notebook outputs before committing** (`make clean-outputs`). Outputs can contain keys, personal data and large binary blobs.
- **Keep sample data small and synthetic.** No real user recordings, identity documents or exam scripts. The repo ships one short Hindi sample clip in `sample_data/`; reuse it rather than adding recordings of real people.
- **If you accidentally push a key, rotate it immediately** in the console, then open a PR removing it. Git history is public.

## API standards

The current models are listed in [`scripts/bodhan_api_rules.json`](scripts/bodhan_api_rules.json). The validator rejects any other `indic-*` / `bodhan-*` id.

| Use this | For | Not this |
|---|---|---|
| `indic-transcribe` | speech to text | `bodhan-asr` |
| `indic-translate` | translation | `bodhan-mt` |
| `indic-transliterate` | transliteration | — |
| `indic-speak` | text to speech | — |
| `indic-ocr` | document OCR | `bodhan-ocr` |

Other conventions:

- Model ids are lower-case and case-sensitive.
- Language codes are plain ISO codes: `hi`, `ta`, `bn`. Not `hi-IN`. Script variants use a suffix where the API defines one: `ks-Deva`, `mni-Beng`, `sd-Arab`.
- Base URL is `https://api.bodhan.ai`. The `/v1/...` routes are OpenAI-compatible, so the OpenAI SDKs work with `base_url` set; `/translate` and `/transliterate` are plain JSON endpoints.
- Handle `429` (rate limit or budget) by backing off; don't loop-retry.
- Keep audio requests to about 30 seconds for both transcription and speech. Chunk longer content and say so in the README.

## Local validation

```bash
make check                                       # everything CI runs
python scripts/validate_recipe.py examples/x     # just one recipe
python scripts/ci_validate.py --all              # every recipe
```

`make check` needs Python 3.10+ and installs `pytest` and `ruff` from `requirements-dev.txt`.

## Pull request process

1. Fill in the pull request template. The checklist is the review checklist.
2. One recipe per PR. Tooling changes go in their own PR.
3. CI must be green. Warnings are fine; errors are not.
4. A maintainer reviews within a week. Expect feedback on README clarity first; that is what most readers see.
5. We squash-merge. Your commits can be messy; your PR title should not be. Use the form `feat(examples): add exam paper reader`.

If you used an AI assistant for a meaningful share of the work, say so in the PR's "AI assistance" section. It is welcome and it helps reviewers.

## Reporting bugs

Use the [bug report template](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues/new?template=bug_report.md). Include the recipe path, the full error, your OS and runtime versions, and whether a plain `curl` to the API also fails. Redact your key.

For security problems, **do not open a public issue**. See [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind, be specific, assume good intent.
