"""Build 108 controls/perturbations from human-translated FLORES-200 text.

The downloaded archive stays outside git. No model generates target text here.
"""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

from bodhan_eval.inject import inject


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    languages = {"en": "eng_Latn", "hi": "hin_Deva", "ta": "tam_Taml", "bn": "ben_Beng"}
    texts = {}
    with tarfile.open(args.archive) as archive:
        for lang, identifier in languages.items():
            name = f"./flores200_dataset/dev/{identifier}.dev"
            texts[lang] = archive.extractfile(name).read().decode().splitlines()
    data = Path(__file__).resolve().parents[1] / "data"
    data.mkdir(exist_ok=True)
    demo, baseline = [], []
    # Deterministically selected source lines, well separated from held-out
    # dataset lines. No checker threshold is fitted to these examples.
    indices = [i for i, line in enumerate(texts["en"]) if 80 <= len(line) <= 200][:18]
    kinds = ["number", "omission", "addition", "wrong_language", "format", "entity"] * 3
    for lang in ("hi", "ta", "bn"):
        for index, kind in zip(indices, kinds, strict=True):
            source, target = texts["en"][index], texts[lang][index]
            glossary = {}
            if kind == "number":
                source += " [41]"
                target += " [41]"
            if kind == "format":
                source += " $x^2 + y^2 = z^2$"
                target += " $x^2 + y^2 = z^2$"
            if kind == "entity":
                source += " (Sample-A)"
                target += " (Sample-A)"
                glossary = {"Sample-A": "Sample-A"}
            row = {"id": f"flores-{index + 1:04d}-{lang}", "group_id": f"flores-{index + 1:04d}",
                   "source": source, "lang": lang, "translation": target, "reference": target,
                   "labels": [], "annotation_source": "human_translation_control_assumption",
                   "provenance": {"dataset": "FLORES-200", "split": "dev", "line": index + 1,
                                  "license": "CC-BY-SA-4.0", "synthetic_wrapper": kind in ("number", "format", "entity")}}
            if glossary:
                row["glossary"] = glossary
            broken = inject(row, kind)
            demo.extend([row, broken])
            clean_variant = {**row, "id": broken["id"]}
            baseline.extend([row, clean_variant])
    for name, records in (("demo.jsonl", demo), ("baseline.jsonl", baseline)):
        with (data / name).open("w") as output:
            for row in records:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {"archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
            "url": "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz",
            "languages": languages, "source_line_indices_1_based": [i + 1 for i in indices],
            "items": len(demo), "clean_controls": 54, "injected": 54,
            "human_quality_labels_added": 0,
            "note": "References and clean controls share human translations. Scores on them are an integrity control, not independent MT quality estimates."}
    (data / "provenance.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Wrote {len(demo)} items in Hindi, Tamil and Bengali")


if __name__ == "__main__":
    main()
