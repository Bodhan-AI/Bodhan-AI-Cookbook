"""Download and verify the separately hosted demo audio and Bodhan cache. No model calls."""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20_000_000


def install_archive(archive: Path, root: Path, manifest: dict) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("asset archive checksum does not match data/demo_assets.json")
    expected = {**manifest["files"], ".demo_cache/cache.sqlite": manifest["cache_sha256"]}
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != set(expected):
            raise ValueError("unexpected or duplicate archive entries")
        contents = {}
        for name, checksum in expected.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("archive path escapes the recipe")
            contents[target] = bundle.read(name)
            if hashlib.sha256(contents[target]).hexdigest() != checksum:
                raise ValueError(f"checksum mismatch for {name}")
        # Validate every entry before writing anything; never follow archive symlinks.
        for target, data in contents.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                handle.write(data)
                temporary = Path(handle.name)
            temporary.replace(target)


def main() -> None:
    manifest = json.loads((ROOT / "data/demo_assets.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="bodhan-demo-") as folder:
        archive = Path(folder) / "assets.zip"
        request = urllib.request.Request(manifest["url"], headers={"User-Agent": "bodhan-eval-demo"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("asset download exceeds the size limit")
                output.write(chunk)
        install_archive(archive, ROOT, manifest)
    print(f"Installed {manifest['audio_clips']} audio clips and {manifest['cached_bodhan_outputs']} cached Bodhan outputs. No model calls.")


if __name__ == "__main__":
    main()
