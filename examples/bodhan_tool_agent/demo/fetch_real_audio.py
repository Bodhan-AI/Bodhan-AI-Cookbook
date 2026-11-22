"""Download a few real speech recordings for the demo tasks, from Google's FLEURS dataset.

    python demo/fetch_real_audio.py

FLEURS (https://huggingface.co/datasets/google/fleurs) is native speakers reading sentences,
released under CC BY 4.0. Each language's test set is one .tar.gz archive. This script
streams the archive and stops as soon as it has the recordings it needs, so it downloads
a few MB, not the whole file.

It writes:
  inputs/voice_note/voice_note.wav  five Hindi recordings joined into one message (about a minute)
  inputs/clips/clip_1.wav ... _5    one recording each: Telugu, Tamil, Telugu, Bengali, Hindi (shuffled)
  references/real_audio.json        the language and correct transcript of each file, kept OUTSIDE
                                    the inputs folder so the agent never sees the answers
  references/ATTRIBUTION.md         credit, as the CC BY 4.0 licence requires

Audio stays out of git (see .gitignore). Link to FLEURS in the README instead.
"""

from __future__ import annotations

import json
import random
import sys
import tarfile
import wave
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
REFERENCES = HERE / "references"
BASE = "https://huggingface.co/datasets/google/fleurs/resolve/main/data"

# FLEURS config names for the languages we need.
CONFIGS = {"hi": "hi_in", "te": "te_in", "ta": "ta_in", "bn": "bn_in"}
NAMES = {"hi": "Hindi", "te": "Telugu", "ta": "Tamil", "bn": "Bengali"}


def read_tsv(lang: str, split: str = "test") -> dict[str, dict]:
    """file name -> {transcript, seconds, gender}. Columns: id, file, raw text, normalised text, phonemes, samples, gender."""
    url = f"{BASE}/{CONFIGS[lang]}/{split}.tsv"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    rows = {}
    for line in resp.text.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        _id, file_name, raw, _norm, _phon, samples, gender = parts[:7]
        try:
            seconds = int(samples) / 16000
        except ValueError:
            continue
        rows[file_name] = {"transcript": raw.strip(), "seconds": round(seconds, 2), "gender": gender.strip()}
    return rows


def stream_wavs(lang: str, wanted: int, rows: dict[str, dict], min_s: float = 5.0, max_s: float = 14.0, split: str = "test") -> list[tuple[str, bytes]]:
    """Read the language's audio archive as a stream and return the first `wanted` recordings of a good length."""
    url = f"{BASE}/{CONFIGS[lang]}/audio/{split}.tar.gz"
    picked: list[tuple[str, bytes]] = []
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = False  # the archive itself is gzip; let tarfile unpack it
        with tarfile.open(fileobj=resp.raw, mode="r|gz") as archive:
            for member in archive:
                name = Path(member.name).name
                info = rows.get(name)
                if not member.isfile() or not info or not min_s <= info["seconds"] <= max_s:
                    continue
                data = archive.extractfile(member).read()
                picked.append((name, data))
                if len(picked) >= wanted:
                    break  # leaving the loop closes the download: no more data is fetched
    if len(picked) < wanted:
        raise RuntimeError(f"Found only {len(picked)} suitable {NAMES[lang]} recordings in the archive.")
    return picked


def read_wav(data: bytes) -> tuple[bytes, int]:
    """Any common WAV (16/24/32-bit PCM or 32/64-bit float, any channels) as 16-bit mono PCM.

    FLEURS stores 32-bit float WAV, which Python's wave module cannot read, so the chunks are parsed here.
    """
    import struct

    import numpy as np

    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("Not a WAV file.")
    pos, fmt, samples = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, channels, rate = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
            if tag == 0xFFFE and len(body) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real format is in the sub-format GUID
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            samples = body
        pos += 8 + size + (size & 1)
    if fmt is None or samples is None:
        raise RuntimeError("WAV file has no fmt or data chunk.")
    tag, channels, rate, bits = fmt
    if tag == 3 and bits in (32, 64):
        x = np.frombuffer(samples[: len(samples) // (bits // 8) * (bits // 8)], dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(samples[: len(samples) // 2 * 2], dtype="<i2") / 32768.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(samples[: len(samples) // 4 * 4], dtype="<i4") / 2147483648.0
    elif tag == 1 and bits == 24:
        raw = np.frombuffer(samples[: len(samples) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / 8388608.0
    else:
        raise RuntimeError(f"Unsupported WAV format {tag} with {bits}-bit samples.")
    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    pcm = (np.clip(x, -1.0, 1.0) * 32767).round().astype("<i2")
    return pcm.tobytes(), rate


def write_wav(path: Path, frames: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    references: dict[str, dict] = {}

    try:
        # Voice note: five Hindi recordings joined with short pauses.
        print("Hindi: downloading the transcript list and 6 recordings ...")
        hindi_rows = read_tsv("hi")
        hindi = stream_wavs("hi", 6, hindi_rows)
        parts, rate = [], 16000
        for _name, data in hindi[:5]:
            frames, rate = read_wav(data)
            parts.append(frames)
        pause = b"\x00\x00" * int(rate * 0.6)
        write_wav(INPUTS / "voice_note" / "voice_note.wav", pause.join(parts), rate)
        references["voice_note/voice_note.wav"] = {
            "language": "hi",
            "transcript": " ".join(hindi_rows[name]["transcript"] for name, _ in hindi[:5]),
            "fleurs_files": [name for name, _ in hindi[:5]],
        }
        print("  inputs/voice_note/voice_note.wav")

        # Clips: two Telugu, one each of Tamil, Bengali and Hindi, in a shuffled order.
        clips: list[tuple[str, str, bytes, dict]] = [("hi", *hindi[5], hindi_rows[hindi[5][0]])]
        for lang, count in (("te", 2), ("ta", 1), ("bn", 1)):
            print(f"{NAMES[lang]}: downloading the transcript list and {count} recording(s) ...")
            rows = read_tsv(lang)
            clips += [(lang, name, data, rows[name]) for name, data in stream_wavs(lang, count, rows)]
        random.Random(7).shuffle(clips)
        for number, (lang, name, data, info) in enumerate(clips, 1):
            frames, rate = read_wav(data)
            write_wav(INPUTS / "clips" / f"clip_{number}.wav", frames, rate)
            references[f"clips/clip_{number}.wav"] = {"language": lang, "transcript": info["transcript"], "fleurs_file": name}
            print(f"  inputs/clips/clip_{number}.wav")
    except (requests.RequestException, RuntimeError, tarfile.TarError) as exc:
        print(f"\nDownload failed: {exc}", file=sys.stderr)
        print("Check your internet connection and try again. If huggingface.co is blocked on your network, use your own recordings instead.", file=sys.stderr)
        return 1

    REFERENCES.mkdir(parents=True, exist_ok=True)
    (REFERENCES / "real_audio.json").write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8")
    (REFERENCES / "ATTRIBUTION.md").write_text(
        "# Attribution\n\n"
        "The demo audio in `inputs/voice_note/` and `inputs/clips/` comes from **FLEURS** by Google "
        "(Conneau et al., 2022, arXiv:2205.12446), https://huggingface.co/datasets/google/fleurs, "
        "licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).\n\n"
        "Changes made: five Hindi test-set recordings were joined into one file with short pauses; "
        "other recordings were renamed. `real_audio.json` lists the original FLEURS file names.\n",
        encoding="utf-8",
    )
    print("\nDone. Correct transcripts are in demo/references/real_audio.json (not visible to the agent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
