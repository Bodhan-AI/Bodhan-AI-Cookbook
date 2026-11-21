"""Prepare a licensed continuous audio excerpt without OCR, ASR, translation or TTS."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import time
from urllib.parse import urlparse
import wave

import httpx

from bodhan_core.media import command, duration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument(
        "--source-file", type=Path, help="Reuse an already downloaded, checksum-verified source."
    )
    parser.add_argument("--source-page", required=True)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--license-url", required=True)
    parser.add_argument(
        "--language", required=True, help="Source-declared language, not detection."
    )
    parser.add_argument("--offset-seconds", type=float, default=0)
    parser.add_argument("--duration-seconds", type=int, default=180)
    parser.add_argument("--expected-sha1", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if urlparse(args.source_url).scheme != "https":
        parser.error("Download from an HTTPS source.")
    if not 0 <= args.offset_seconds <= 7200 or not 1 <= args.duration_seconds <= 7200:
        parser.error("Use a non-negative offset and a positive duration, at most two hours.")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("Choose a new empty output directory; preserve earlier evidence.")
    inputs, evidence = args.out / "input", args.out / "provenance"
    inputs.mkdir(parents=True)
    evidence.mkdir()
    source = evidence / "source.media"
    if args.source_file:
        if not args.source_file.is_file() or args.source_file.stat().st_size > 100 * 1024 * 1024:
            raise SystemExit("Source must be a regular file of at most 100MB.")
        shutil.copyfile(args.source_file, source)
    else:
        started = time.monotonic()
        headers = {"User-Agent": "BodhanTaskAgentEvaluation/0.1 (https://github.com/BishalJena)"}
        with httpx.stream(
            "GET", args.source_url, headers=headers, follow_redirects=True, timeout=30
        ) as response:
            response.raise_for_status()
            with source.open("xb") as stream:
                size = 0
                for block in response.iter_bytes():
                    size += len(block)
                    if size > 100 * 1024 * 1024 or time.monotonic() - started > 120:
                        raise SystemExit("Download exceeds the 100MB/120s acquisition bound.")
                    stream.write(block)
    source_bytes = source.read_bytes()
    if hashlib.sha1(source_bytes).hexdigest() != args.expected_sha1.lower():
        raise SystemExit("Source differs from its published checksum; no excerpt generated.")
    source_seconds = duration(source)
    if args.offset_seconds + args.duration_seconds > source_seconds:
        raise SystemExit("Requested continuous excerpt exceeds the source duration.")
    target = inputs / "excerpt.wav"
    start_sample = round(args.offset_seconds * 16000)
    end_sample = start_sample + args.duration_seconds * 16000
    conversion = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(source.resolve()),
        "-af",
        f"aresample=16000,atrim=start_sample={start_sample}:end_sample={end_sample},asetpts=PTS-STARTPTS",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target.resolve()),
    ]
    command(conversion, timeout=60)
    with wave.open(str(target), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise SystemExit("Excerpt does not have the required mono16kHz PCM16 format.")
        if audio.getnframes() != args.duration_seconds * 16000:
            raise SystemExit("Decoded PCM does not have the exact requested duration.")
    manifest = {
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_url": args.source_url,
        "source_page": args.source_page,
        "creator": args.creator,
        "license_url": args.license_url,
        "language": args.language,
        "language_source": "Caller/source metadata, not model detection",
        "source_sha1": args.expected_sha1.lower(),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_seconds": source_seconds,
        "offset_seconds": args.offset_seconds,
        "offset_reference": "Decoded audio after16kHz resampling, selected by exact sample indices rather than container packet timestamps.",
        "start_sample": start_sample,
        "end_sample": end_sample,
        "excerpt_seconds": duration(target),
        "conversion_argv": conversion,
        "excerpt_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "modifications": "Continuous excerpt, audio extraction, mono16kHz PCM conversion. No looping, synthesis, speed change or silence padding.",
        "model_calls": 0,
        "evaluation_status": "Prepared input; not yet transcribed/translated or accepted as a completed model demo.",
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (evidence / "ATTRIBUTION.md").write_text(
        f"# Recording attribution\n\nCreator: {args.creator}\n\nSource: {args.source_page}\n\n"
        f"License: {args.license_url}\n\nChanges: {manifest['modifications']}\n\n"
        "The excerpt retains the source license. No endorsement is implied. This is input preparation, not a completed Bodhan run.\n"
    )
    print(
        json.dumps({"input": str(target), "seconds": manifest["excerpt_seconds"], "model_calls": 0})
    )


if __name__ == "__main__":
    main()
