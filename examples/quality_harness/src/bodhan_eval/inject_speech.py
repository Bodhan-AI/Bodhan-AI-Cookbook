"""Speech error injector (plan §15): seeded edits of generated audio, each with an exact label.

    python -m bodhan_eval.inject_speech --in report/items.jsonl --out data/speech_injected.jsonl \\
        --audio-dir data/audio-injected --per-category 10 --negatives 10 --seed 42

Every item in the report that has audio becomes a base (pointing at its audio); variants
`<base>~<op>` get an edited copy of that audio and one `annotator: "injector"` label. The text is
the base's, so only the speech checks see a difference (and only transcription is paid for). Edits
cut at pauses where the clip has them, so a removed or repeated stretch is a phrase, not half a word.
Labels point at the translation in proportion to time (no word timestamps), plus the time span.

Ops: truncation (keep the first 50-80%), omission (drop a phrase), repetition (say a phrase twice),
silence (insert 1.5-3 s), noise (white noise at 0 dB SNR for 1-2 s), clipping (gain x4 into the
limits). Hard negatives (labels []): quieter (x0.7), louder (x1.5, no clipping), 0.3 s more
silence at the end, faint hiss 40 dB under the speech.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np

from . import audio as A
from .speech import _span

OPS = ("truncation", "omission", "repetition", "silence", "noise", "clipping")
CATEGORY = {"truncation": "speech_omission", "omission": "speech_omission", "repetition": "speech_repetition",
            "silence": "speech_garbled", "noise": "speech_garbled", "clipping": "speech_garbled"}
NEGATIVES = ("quieter", "louder", "end_silence", "faint_hiss")


def _phrase(a: A.Audio, rng: random.Random, lo: float, hi: float) -> tuple[float, float] | None:
    """A stretch between two pauses (or clip edges), lo..hi seconds long, not at the very start."""
    bounds = [0.0] + [(s + e) / 2 for s, e in A.silences(a, 0.08)] + [a.duration]
    options = [(s, e) for s, e in zip(bounds, bounds[1:]) if lo <= e - s <= hi and s > 0.3]
    options += [(s, e) for i, s in enumerate(bounds) for e in bounds[i + 2:i + 4] if lo <= e - s <= hi and s > 0.3]
    if options:
        return rng.choice(sorted(set(options)))
    if a.duration < hi + 1:
        return None
    s = rng.uniform(0.5, a.duration - hi)
    return s, s + rng.uniform(lo, hi)


def _rms(a: A.Audio) -> float:
    voiced = a.samples[np.abs(a.samples) > 0.01]
    return float(np.sqrt((voiced ** 2).mean())) if len(voiced) else 0.1


def edit(op: str, a: A.Audio, rng: random.Random) -> tuple[A.Audio, tuple[float, float], str] | None:
    """(edited audio, time span of the fault in the *original* clip's timeline, severity)."""
    d, rate = a.duration, a.rate
    np_rng = np.random.default_rng(rng.randrange(2 ** 32))
    if op == "truncation":
        keep = rng.uniform(0.5, 0.8) * d
        pauses = [(s + e) / 2 for s, e in A.silences(a, 0.08) if 0.45 * d <= (s + e) / 2 <= 0.85 * d]
        keep = min(pauses, key=lambda t: abs(t - keep)) if pauses else keep
        return A.cut(a, 0, keep), (keep, d), "critical" if keep < 0.6 * d else "major"
    if op == "omission":
        p = _phrase(a, rng, 0.8, 3.0)
        return (A.concat(A.cut(a, 0, p[0]), A.cut(a, p[1], d)), p, "major") if p and p[1] < d - 0.3 else None
    if op == "repetition":
        p = _phrase(a, rng, 0.6, 2.0)
        return (A.concat(A.cut(a, 0, p[1]), A.cut(a, *p), A.cut(a, p[1], d)), p, "major") if p else None
    if op == "silence":
        pauses = [(s + e) / 2 for s, e in A.silences(a, 0.05) if 0.2 * d <= (s + e) / 2 <= 0.8 * d] or [d / 2]
        t, gap = rng.choice(pauses), rng.uniform(1.5, 3.0)
        z = A.Audio(np.zeros(int(gap * rate), np.float32), rate)
        return A.concat(A.cut(a, 0, t), z, A.cut(a, t, d)), (t, t), "major"
    if op == "noise":
        length = min(rng.uniform(1.0, 2.0), 0.6 * d)
        if length < 0.6:
            return None
        s = rng.uniform(0.1 * d, d - length - 0.05 * d)
        x = a.samples.copy()
        i, j = int(s * rate), int((s + length) * rate)
        x[i:j] += np_rng.normal(0, _rms(a), j - i).astype(np.float32)
        return A.Audio(np.clip(x, -1, 1), rate), (s, s + length), "major"
    if op == "clipping":
        return A.Audio(np.clip(a.samples * 4, -1, 1), rate), (0.0, d), "major"
    if op == "quieter":
        return A.Audio(a.samples * 0.7, rate), (0, 0), ""
    if op == "louder":
        peak = float(np.abs(a.samples).max()) or 1.0
        return A.Audio(a.samples * min(1.5, 0.95 / peak), rate), (0, 0), ""
    if op == "end_silence":
        return A.concat(a, A.Audio(np.zeros(int(0.3 * rate), np.float32), rate)), (0, 0), ""
    if op == "faint_hiss":
        x = a.samples + np_rng.normal(0, _rms(a) * 10 ** (-40 / 20), len(a.samples)).astype(np.float32)
        return A.Audio(x, rate), (0, 0), ""
    raise ValueError(op)


def inject(records: list[dict], dataset_dir: Path, audio_dir: Path, per_category: int, negatives: int, seed: int) -> list[dict]:
    bases = sorted((r for r in records if r.get("speech", {}).get("duration_s")), key=lambda r: (r["id"], r["lang"]))
    rel = lambda p: os.path.relpath(Path(p).resolve(), dataset_dir.resolve())
    keep = ("id", "lang", "source", "translation", "reference", "translation_origin")
    rows = [{**{k: r[k] for k in keep if r.get(k) is not None}, "audio": rel(r["speech"]["audio"])} for r in bases]
    by_lang: dict[str, list[dict]] = {}
    for r in bases:
        by_lang.setdefault(r["lang"], []).append(r)
    for what, want in [(op, per_category) for op in OPS] + [(op, -(-negatives // len(NEGATIVES))) for op in NEGATIVES]:
        rng = random.Random(f"{seed}:{what}")
        queues = [rng.sample(v, len(v)) for _, v in sorted(by_lang.items())]
        order = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
        got = 0
        for r in order:
            if got == want:
                break
            a = A.load(Path(r["speech"]["audio"]))
            out = edit(what, a, random.Random(f"{seed}:{what}:{r['id']}:{r['lang']}"))
            if out is None:
                continue
            new, (t0, t1), severity = out
            path = A.save(new, audio_dir / f"{r['id']}~{what}.{r['lang']}.wav")
            labels = [] if what in NEGATIVES else [{
                "type": CATEGORY[what], "severity": severity, "side": "translation",
                "span": list(_span(t0, t1, a.duration, len(r["translation"]))), "time": [round(t0, 2), round(t1, 2)],
                "annotator": "injector", "op": what}]
            rows.append({**{k: r[k] for k in keep if r.get(k) is not None}, "id": f"{r['id']}~{what}", "audio": rel(path),
                         "base": r["id"], "injection": {"category": CATEGORY.get(what, "clean"), "op": what}, "labels": labels})
            got += 1
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject_speech")
    p.add_argument("--in", dest="inp", type=Path, required=True, help="a report's items.jsonl (items with generated audio)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--audio-dir", type=Path, required=True, help="where the edited audio goes (keep it out of git)")
    p.add_argument("--per-category", type=int, default=10, help="variants per op")
    p.add_argument("--negatives", type=int, default=12)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    records = [json.loads(line) for line in args.inp.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = inject(records, args.out.parent, args.audio_dir, args.per_category, args.negatives, args.seed)
    with args.out.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    n = sum(1 for r in rows if "base" in r)
    print(f"wrote {len(rows) - n} bases and {n} variants to {args.out}")


if __name__ == "__main__":
    main()
