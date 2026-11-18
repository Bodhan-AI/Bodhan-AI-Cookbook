"""Refresh SHA-256 values and audio durations for authored eval media."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "media_manifest.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audio_duration(path: Path) -> float:
    ffprobe = "/opt/homebrew/bin/ffprobe"
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return round(float(result.stdout.strip()), 3)


def main() -> None:
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        path = ROOT / row["path"]
        if path.is_dir():
            children = sorted(p for p in path.rglob("*") if p.is_file())
            for member in row.get("members", []):
                child = ROOT / member["path"]
                member["sha256"] = sha256(child)
                if child.suffix.lower() == ".wav":
                    member["duration_seconds"] = audio_duration(child)
            row["sha256"] = hashlib.sha256("".join(f"{p.relative_to(ROOT).as_posix()}:{sha256(p)}\n" for p in children).encode()).hexdigest()
        elif path.is_file():
            row["sha256"] = sha256(path)
            if path.suffix.lower() == ".wav":
                row["duration_seconds"] = audio_duration(path)
    MANIFEST.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(f"Refreshed {len(rows)} media manifest records")


if __name__ == "__main__":
    main()
