# Human review and calibration

The injector knows what it changed, but it cannot certify that the underlying translation is
correct. A person who understands English and the target language must review examples. If one
person cannot assess every language, use separate qualified reviewers and identify each reviewer.
Do not label unreviewed items as clean.

Prepare a blind sample (the source dataset and generated audio are not changed):

```bash
python scripts/human_review.py prepare --dataset data/demo.jsonl --out review.jsonl --per-group 3
```

Each line is a JSON object. Inspect `source` and `translation` independently. For each reviewed
item, set `reviewed` to `true`, set `annotator` to the actual reviewer's name or identifier, and
replace `labels: null` with a list. Use `[]` only when the person has checked and found no error.
Leave anything not reviewed unchanged. The template intentionally does not show injector labels.

For an error, add an object such as:

```json
{"type":"number_unit", "severity":"major", "span_space":"target", "span":[4,6]}
```

Use the actual error and actual offsets, not the example offsets. Spans are zero-based, half-open
Python character indices in the original source or translation. If a reliable span cannot be
provided, omit `span`; localization cannot then be measured for that label. Supported categories
are documented in the README. A sentence omission is often best located in `source` space.

Export reviewed records only; the exporter validates categories, spans, and reviewer identifiers,
and refuses to overwrite an existing output file:

```bash
python scripts/human_review.py export --review review.jsonl --out human_labels.jsonl
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out reviewed_report \
  --calibration human_labels.jsonl
```

That command may invoke configured models. Use a separate calibration sample and evaluation sample
to assess generalization; metrics computed on the same items used to choose thresholds are optimistic.
When `--calibration` is supplied, only matching IDs in that file contribute to calibration; labels
already present on other dataset items are excluded rather than mixed with the human review.
When publishing, confirm reviewers consent to attribution and do not include confidential content.
