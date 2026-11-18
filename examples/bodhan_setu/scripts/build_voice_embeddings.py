"""Build a local DSP voice-reference library from the Bodhan voice catalog.

This script makes one Indic-Speak request per voice. Run with --dry-run to see
the request count and estimated cost without contacting Bodhan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bodhan_mcp.voice_match import audio_features_bytes

load_dotenv()

ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "scripts" / "bodhan_api_rules.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "voice_embeddings.json"
CALIBRATION_TEXT = "The quick brown fox jumps over the lazy dog. Please call me when you arrive."
SPEAK_RATE_PER_10K_CHARS_INR = 6.0


def voice_rows(rules: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for language, groups in rules["models"]["indic-speak"]["voices"].items():
        for gender, names in groups.items():
            result.extend({"voice": name, "language": language, "gender": gender} for name in names)
    return result


def generate_library(output: Path, *, dry_run: bool = False, keep_samples: Path | None = None) -> dict[str, Any]:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    voices = voice_rows(rules)
    expected_characters = len(CALIBRATION_TEXT) * len(voices)
    estimate = round(expected_characters * SPEAK_RATE_PER_10K_CHARS_INR / 10_000, 6)
    print(f"Voices: {len(voices)}; calibration characters: {expected_characters}; estimated Indic-Speak cost: ₹{estimate:.6f}")
    if dry_run:
        return {"voice_count": len(voices), "estimated_characters": expected_characters, "estimated_cost_inr": estimate}

    key = os.getenv("BODHAN_SPEAK_KEY")
    if not key or key.startswith("<"):
        raise RuntimeError("Set BODHAN_SPEAK_KEY before building the voice library")
    base_url = os.getenv("BODHAN_BASE_URL", rules["base_url"]).rstrip("/")
    if keep_samples:
        keep_samples.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    records_by_voice: dict[str, dict[str, Any]] = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            records_by_voice = {row["voice"]: row for row in existing.get("voices", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            records_by_voice = {}
    pending = [row for row in voices if row["voice"] not in records_by_voice]
    print(f"Already embedded: {len(records_by_voice)}; remaining API calls: {len(pending)}")

    def save_checkpoint() -> None:
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "calibration_language": "en",
            "calibration_text": CALIBRATION_TEXT,
            "method": "DSP statistics: pitch autocorrelation, RMS, zero-crossing rate, and spectral-tilt proxy; not a neural speaker embedding",
            "features": ["median_pitch_hz", "pitch_iqr_hz", "voiced_fraction", "mean_rms_dbfs", "zero_crossing_rate", "spectral_tilt_proxy"],
            "voices": [records_by_voice[row["voice"]] for row in voices if row["voice"] in records_by_voice],
            "complete": len(records_by_voice) == len(voices),
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with requests.Session() as session:
        for index, row in enumerate(pending, start=1):
            body = {
                "model": "indic-speak",
                "input": CALIBRATION_TEXT,
                "voice": row["voice"],
                "instructions": json.dumps({"lang": "en"}),
            }
            response = None
            for attempt in range(7):
                try:
                    response = session.post(
                        f"{base_url}/v1/audio/speech",
                        headers={"Authorization": f"Bearer {key}"},
                        json=body,
                        timeout=120,
                    )
                except requests.RequestException:
                    if attempt == 6:
                        raise
                    time.sleep(2 ** attempt)
                    continue
                if response.ok:
                    break
                if response.status_code < 500 and response.status_code != 429:
                    raise RuntimeError(f"Indic-Speak failed for voice {row['voice']} with HTTP {response.status_code}: {response.text[:300]}")
                if attempt == 6:
                    raise RuntimeError(f"Indic-Speak retries exhausted for voice {row['voice']} (HTTP {response.status_code})")
                retry_after = response.headers.get("Retry-After")
                delay = min(60, int(retry_after)) if retry_after and retry_after.isdigit() else min(60, 2 ** attempt)
                time.sleep(delay)
            assert response is not None and response.ok
            features = audio_features_bytes(response.content)
            records_by_voice[row["voice"]] = {**row, "features": features}
            save_checkpoint()
            if keep_samples:
                safe_name = "".join(char.lower() if char.isalnum() else "_" for char in row["voice"]).strip("_")
                (keep_samples / f"{row['language']}_{safe_name}.wav").write_bytes(response.content)
            print(f"[{len(records_by_voice)}/{len(voices)}] embedded {row['voice']} ({row['language']})")

    save_checkpoint()
    print(f"Saved {len(records_by_voice)} voice embeddings to {output}")
    return {"voice_count": len(records_by_voice), "estimated_cost_inr": estimate, "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="show request count and estimated TTS cost; make no API calls")
    parser.add_argument("--keep-samples", type=Path, help="optionally save generated reference WAVs in this directory")
    args = parser.parse_args()
    generate_library(args.output, dry_run=args.dry_run, keep_samples=args.keep_samples)


if __name__ == "__main__":
    main()
