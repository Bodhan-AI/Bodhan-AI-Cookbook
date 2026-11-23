"""Create labelled speech faults from supplied clean audio without a model call.

Run with: python -m bodhan_eval.speech_inject --dataset clean.jsonl --out cases.jsonl
    --audio-dir cases_audio --kind omission|repetition|garbled
Input items must include id, source, lang, translation and audio. Generated audio must not
be committed; it is written to the requested directory. Original audio is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf


def alter_audio(audio: np.ndarray, rate: int, kind: str) -> np.ndarray:
    if len(audio) < rate:
        raise ValueError("audio must be at least one second")
    if kind == "omission":
        return audio[:len(audio) // 2]
    if kind == "repetition":
        return np.concatenate((audio, audio), axis=0)
    if kind == "garbled":
        midpoint = len(audio) // 2
        silence = np.zeros((rate * 4, *audio.shape[1:]), dtype=audio.dtype)
        return np.concatenate((audio[:midpoint], silence, audio[midpoint:]), axis=0)
    raise ValueError("unsupported error kind")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--kind", required=True, choices=("omission", "repetition", "garbled"))
    args = parser.parse_args(argv)
    dataset, out, audio_dir = Path(args.dataset), Path(args.out), Path(args.audio_dir)
    if out.exists():
        parser.error("output dataset already exists")
    audio_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("audio") or not row.get("translation") or row.get("labels"):
            continue
        source = Path(row["audio"])
        source = source if source.is_absolute() else dataset.parent / source
        audio, rate = sf.read(source, dtype="float32", always_2d=True)
        modified = alter_audio(audio, rate, args.kind)
        # Use a sequence number instead of an input id as a path component.
        path = audio_dir / f"{len(rows):06d}_{args.kind}.wav"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
        sf.write(path, modified, rate)
        row["audio"] = os.path.relpath(path.resolve(), out.resolve().parent)
        row["labels"] = [{"type": "speech_" + args.kind, "severity": "major",
                          "label_source": "injector"}]
        row["label_source"] = "injector"
        rows.append(row)
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} labelled audio cases to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
