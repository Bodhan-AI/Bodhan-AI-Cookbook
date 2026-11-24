"""Download and checksum the synthetic benchmark media from its public HF dataset."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import requests


EVALS_DIR = Path(__file__).resolve().parent
RECIPE_DIR = EVALS_DIR.parent
MANIFEST = EVALS_DIR / "media_manifest.jsonl"
DATASET_ID = "1rsh/bodhan-setu-mcp-evals"
BASE_URL = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/main/"


def _assets() -> dict[str, str]:
    assets: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        path = str(row.get("path", ""))
        if path and not path.endswith("/"):
            assets[path] = str(row.get("sha256", ""))
        for member in row.get("members", []):
            assets[str(member["path"])] = str(member.get("sha256", ""))
    return assets


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_media() -> list[Path]:
    session = requests.Session()
    changed: list[Path] = []
    for manifest_path, expected_hash in sorted(_assets().items()):
        if not manifest_path.startswith("media/") or ".." in Path(manifest_path).parts:
            raise ValueError(f"Unsafe media path in manifest: {manifest_path!r}")
        destination = EVALS_DIR / manifest_path
        if destination.is_file() and (not expected_hash or _sha256(destination) == expected_hash):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = BASE_URL + manifest_path
        response = session.get(url, stream=True, timeout=(10, 120))
        response.raise_for_status()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".media-", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                        digest.update(chunk)
            actual_hash = digest.hexdigest()
            if expected_hash and actual_hash != expected_hash:
                raise ValueError(f"SHA-256 mismatch for {manifest_path}: expected {expected_hash}, got {actual_hash}")
            os.replace(temporary, destination)
            changed.append(destination)
        finally:
            temporary.unlink(missing_ok=True)
            response.close()
    return changed


def main() -> int:
    try:
        changed = prepare_media()
    except Exception as exc:
        print(f"Could not prepare evaluation media: {exc}", file=sys.stderr)
        print(f"Dataset: https://huggingface.co/datasets/{DATASET_ID}", file=sys.stderr)
        return 1
    print(f"Verified {len(_assets())} synthetic media files; downloaded {len(changed)}.")
    print(f"Media directory: {EVALS_DIR / 'media'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
