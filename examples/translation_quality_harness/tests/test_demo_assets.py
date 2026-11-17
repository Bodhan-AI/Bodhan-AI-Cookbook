"""Demo downloads validate all bytes before extraction and preserve the live cache."""

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest


def test_verified_assets_use_separate_cache_and_reject_bad_content(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/fetch_demo_assets.py"
    spec = importlib.util.spec_from_file_location("fetch_demo_assets", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = tmp_path / "demo.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(".demo_cache/cache.sqlite", b"demo cache")
        bundle.writestr("data/audio/test.wav", b"audio")
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    manifest = {"sha256": sha(archive.read_bytes()), "cache_sha256": sha(b"demo cache"), "files": {"data/audio/test.wav": sha(b"wrong")}}
    root = tmp_path / "recipe"
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.install_archive(archive, root, manifest)
    assert not root.exists()
    live = root / ".bodhan_cache/cache.sqlite"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"existing paid outputs")
    manifest["files"]["data/audio/test.wav"] = sha(b"audio")
    module.install_archive(archive, root, manifest)
    assert live.read_bytes() == b"existing paid outputs"
    assert (root / ".demo_cache/cache.sqlite").read_bytes() == b"demo cache"
