"""Build an injected dataset: about `rate` of the items get exactly one known error, the rest
are kept unchanged as controls.

Label scope (written on every row, used by calibration):
  injected        only the planted error is labelled; the untouched rest of the system output
                  may still contain real errors, so other flags are not automatically wrong.
  assumed_clean   unchanged system output, labelled clean ([]). An assumption: precision
                  measured on these is an upper bound, and hand labels give the true figure.
Rows that already have labels are copied unchanged.
"""

from __future__ import annotations

import io
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import soundfile as sf

from bodhan_eval.checks.audio import load_audio
from bodhan_eval.checks.translation import source_entities
from bodhan_eval.clients.http import APIError, CacheMiss
from bodhan_eval.cost import BudgetExceeded
from bodhan_eval.dataset import Item
from bodhan_eval.inject.audio import AUDIO
from bodhan_eval.inject.text import PROGRAMMATIC, RETRANSLATE, Ctx, Translator
from bodhan_eval.languages import Language
from bodhan_eval.normalize import split_sentences


def item_row(it: Item, base: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": it.id, "lang": it.lang, "source": it.source}
    if it.reference:
        row["reference"] = it.reference
    if it.translation is not None:
        row["translation"] = it.translation
    if it.audio is not None:
        row["audio"] = str(it.audio.resolve())
    if it.labels is not None:
        row["labels"] = [lb.raw for lb in it.labels]
    row.update(it.extra)
    return row


def _write_wav(path: Path, x, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    path.write_bytes(buf.getvalue())


async def inject_dataset(
    items: list[Item],
    registry: dict[str, Language],
    out_dir: Path,
    *,
    rate: float = 0.5,
    seed: int = 13,
    client: Translator | None = None,
    audio_share: float = 0.4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (rows, stats). Audio files for audio injections go to out_dir/audio/."""
    pools: dict[str, list[str]] = defaultdict(list)
    ent_pool: set[str] = set()
    for it in items:
        if it.translation:
            pools[it.lang] += [
                (it.id, it.translation[s:e]) for s, e in split_sentences(it.translation)
            ]
        ent_pool.update(e for e, _ in source_entities(it.source))

    rows, stats, failed = [], Counter(), Counter()
    for it in items:
        row = item_row(it)
        rng = random.Random(f"{seed}:{it.id}")
        if it.labels is not None or not it.translation:
            rows.append(row)
            stats["copied" if it.labels is not None else "no_translation"] += 1
            continue
        if rng.random() >= rate:
            row.update(labels=[], label_scope="assumed_clean")
            rows.append(row)
            stats["control"] += 1
            continue

        lang = registry[it.lang]
        done = None
        if it.audio is not None and rng.random() < audio_share:
            done = _inject_audio(it, row, rng, out_dir)
        if done is None:
            ctx = Ctx(
                it.source,
                it.translation,
                it.lang,
                lang.translate_code,
                [s for i, s in pools[it.lang] if i != it.id],
                sorted(ent_pool - {e for e, _ in source_entities(it.source)}),
            )
            done = await _inject_text(ctx, row, rng, client)
        if done is None:
            failed["no applicable injection"] += 1
            row.update(labels=[], label_scope="assumed_clean")
            stats["control"] += 1
        else:
            stats[f"injected:{done}"] += 1
        rows.append(row)
    return rows, {"counts": dict(sorted(stats.items())), "failed": dict(failed)}


async def _inject_text(ctx: Ctx, row: dict, rng: random.Random, client) -> str | None:
    kinds = list(PROGRAMMATIC) + (list(RETRANSLATE) if client is not None else [])
    rng.shuffle(kinds)
    for kind in kinds:
        if kind in PROGRAMMATIC:
            inj = PROGRAMMATIC[kind](ctx, rng)
        else:
            try:
                inj = await RETRANSLATE[kind](ctx, rng, client)
            except (BudgetExceeded, CacheMiss, APIError):
                continue  # paid re-translation unavailable: try another kind
        if inj is None:
            continue
        row["id"] = f"{row['id']}__{kind}"
        row["translation"] = inj.translation
        if "audio" in row:  # the recording still says the original text
            row["speech_text"] = ctx.translation
        row.update(labels=[inj.label], label_scope="injected", injected_from=ctx_id(row))
        return kind
    return None


def ctx_id(row: dict) -> str:
    return row["id"].rsplit("__", 1)[0]


def _inject_audio(it: Item, row: dict, rng: random.Random, out_dir: Path) -> str | None:
    try:
        x, sr = load_audio(it.audio)
    except Exception:
        return None
    text = it.extra.get("speech_text") or it.translation or ""
    kinds = list(AUDIO)
    rng.shuffle(kinds)
    for kind in kinds:
        res = AUDIO[kind](x, sr, len(text), rng)
        if res is None:
            continue
        y, label = res
        new_id = f"{it.id}__{kind}"
        path = out_dir / "audio" / f"{new_id}.wav"
        _write_wav(path, y, sr)
        row.update(
            id=new_id,
            audio=str(path.resolve()),
            labels=[label],
            label_scope="injected",
            injected_from=it.id,
        )
        return kind
    return None


def write_rows(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
