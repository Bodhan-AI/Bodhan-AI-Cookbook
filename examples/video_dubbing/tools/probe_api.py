"""Probe the hosted API for timestamp support.

Run this once, with a key, before trusting any assumption about the response
shape. It sends the same clip with several plausible ``response_format`` values
and prints the raw JSON plus the rate-limit headers, so the pipeline can be
adapted to what the API actually returns instead of what we hoped.

    python tools/probe_api.py --audio clip.wav --language en
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bodhan_dub.config import load_env_file  # noqa: E402


def _bootstrap_env() -> None:
    load_env_file()


VARIANTS: list[dict[str, str]] = [
    {},
    {"response_format": "json"},
    {"response_format": "verbose_json"},
    {"response_format": "verbose_json", "timestamp_granularities[]": "segment"},
    {"response_format": "verbose_json", "timestamp_granularities[]": "word"},
    {"response_format": "srt"},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--language", default="en")
    parser.add_argument("--model", default="indic-transcribe")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    # Load the same .env the pipeline uses, so this script works from anywhere.
    _bootstrap_env()

    key = (
        os.environ.get("BODHAN_TRANSCRIBE_KEY")
        or os.environ.get("BODHAN_TRANSCRIBE_API_KEY")
        or os.environ.get("BODHAN_API_KEY")
    )
    if not key:
        print("set BODHAN_TRANSCRIBE_KEY (or BODHAN_TRANSCRIBE_API_KEY) first", file=sys.stderr)
        return 2
    if not args.audio.exists():
        print(f"audio file not found: {args.audio}", file=sys.stderr)
        return 2

    base_url = args.base_url or os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")
    payload = args.audio.read_bytes()
    url = f"{base_url.rstrip('/')}/v1/audio/transcriptions"

    for index, extra in enumerate(VARIANTS, start=1):
        form = {"model": args.model, "language": args.language, **extra}
        print(f"\n=== variant {index}: {extra or 'defaults'} ===")
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (args.audio.name, payload, "audio/wav")},
                data=form,
                timeout=120,
            )
        except requests.RequestException as exc:
            print(f"  transport error: {exc}")
            continue

        print(f"  status: {response.status_code}")
        for header in ("x-ratelimit-limit-requests", "x-ratelimit-limit-parallel-requests"):
            if header in response.headers:
                print(f"  {header}: {response.headers[header]}")
        try:
            body = response.json()
        except ValueError:
            print(f"  body (not JSON): {response.text[:400]!r}")
            continue
        keys = sorted(body) if isinstance(body, dict) else type(body).__name__
        print(f"  top-level keys: {keys}")
        print("  " + json.dumps(body, ensure_ascii=False)[:1200].replace("\n", " "))

    print("\nIf any variant returns segment or word timings, wire it into")
    print("BodhanClient.transcribe and prefer it over VAD-derived boundaries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
