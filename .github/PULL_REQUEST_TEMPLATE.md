## Summary

<!-- What does this recipe or change do, and why is it useful? 2–4 sentences. -->

**Type:** <!-- new recipe / fix to an existing recipe / tooling / docs -->
**Path:** <!-- e.g. examples/voice_notes_transcriber/ -->
**Bodhan models used:** <!-- indic-transcribe, indic-translate, indic-transliterate, indic-speak, indic-ocr -->

## Checklist

- [ ] I started from `examples/TEMPLATE/` (new recipes) and the folder matches the required layout
- [ ] `README.md` explains what the recipe does, how to set it up, and how to run it
- [ ] Dependencies are pinned (`name==version`) in `requirements.txt` / `package.json`
- [ ] `.env.example` lists every variable; **no real key anywhere in the diff**
- [ ] Notebook outputs are cleared (`make clean-outputs`)
- [ ] Only current model ids from `scripts/bodhan_api_rules.json` are used
- [ ] `make check` passes locally
- [ ] I ran the recipe end to end against `https://api.bodhan.ai` with my own key

## Screenshots / sample output

<!-- Optional but appreciated: a screenshot, a short transcript, or a link to a demo. Do not paste raw audio or PII. -->

## AI assistance

<!-- If a meaningful share of this contribution was written with an AI tool, say which one and what it did. This is for transparency, not a penalty. -->
