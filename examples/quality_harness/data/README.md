# Demo provenance and preparation

Use the [Phase 3 workflow](../docs/phase3_workflow.md) for the current commands: blind export/import, hash-checked label application, strict human-clean injection and evidence audit. The quick commands below omit judge prices/private configuration and are a pipeline sketch, not a complete live invocation. `benchmark_smoke.jsonl` contains the nine supplied-output records from the smoke fixture, exclusively for complete offline replay validation; `live_smoke.jsonl` contains three original English inputs for a real Bodhan pilot. Neither fixture supplies fabricated model outputs or human quality results.

`demo.jsonl` contains 120 original synthetic English records: 40 distinct sources, each targeting Hindi, Tamil and Bengali. The source author dedicates these English fixtures to the public domain under CC0-1.0. They contain educational, procedural, numeric, negation, Markdown, table and LaTeX examples. They are not a representative sample of production traffic.

No Indian-language translations were composed by a general LLM for this demo. Source-only records have neither outputs nor labels. Generate the outputs using Bodhan before running the injector. Do not treat an unreviewed Bodhan output as clean ground truth. A human must review clean parents; annotate these with `label_origin: human:<annotator>` and `labels: []`, or explicit defect labels. The injector refuses parents already labelled erroneous and leaves unlabelled parents unlabelled.

```bash
python -m bodhan_eval.demo --out data/demo.jsonl
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/generated --generate --max-cost-inr 50
# Review outputs/generated/outputs.jsonl, then save clean parents to outputs/clean.jsonl.
python -m bodhan_eval.inject --dataset outputs/clean.jsonl --out outputs/injected.jsonl
python -m bodhan_eval --dataset outputs/injected.jsonl --langs hi,ta,bn --out outputs/evaluated
```

Supply judge price flags on live commands; see README. The injector preserves the clean parent's ID across languages and assigns all sibling mutations to one deterministic split. Numeric, duplicated-text, literal-markup and protected-term mutations have precise offsets. Final-paragraph deletion assumes structurally corresponding paragraphs and requires review. Speech variants remove the final quarter, append the final quarter or replace samples with digital silence, with seconds-based labels. These operations perturb existing Bodhan audio, not synthesize speech with another model. Generated files/audio remain git-ignored.

`smoke.jsonl` is a ten-record deterministic test fixture. Its digits and strings are test inputs, not Bodhan translations, and its programmatic labels are not human annotations. It cannot establish multilingual semantic accuracy.
