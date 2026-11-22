"""Shared content identity for human review, benchmarks and submission evidence."""

import json
from pathlib import Path

from .models import canonical, digest


def read_items(path):
    # Import lazily to keep the evaluation runner independent of audit tooling.
    from .runner import validate

    rows, seen = [], set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            validate(row)
            key = (row["id"], row["lang"])
            if key in seen:
                raise ValueError("duplicate item identity")
        except (ValueError, TypeError, KeyError):
            raise ValueError(f"invalid or duplicate dataset record at line {number}") from None
        seen.add(key)
        rows.append(row)
    return rows


def content_identity(item, directory):
    """Labels, file locations and model verdicts cannot influence review identity."""
    # ASR is a checker, not the system output being labelled. Human speech spans
    # use intended translation text or actual audio seconds, never ASR offsets.
    identity = {name: item.get(name) for name in ("id", "lang", "source", "translation", "reference", "terms")}
    identity["version"] = 1
    identity["parent_id"] = item.get("parent_id") or item["id"]
    identity["terms"] = item.get("terms") or {}
    identity["audio_sha256"] = digest((Path(directory) / item["audio"]).read_bytes()) if item.get("audio") else None
    return identity


def content_fingerprint(item, directory):
    return digest(canonical(content_identity(item, directory)).encode("utf-8"))


def file_hash(path):
    return digest(Path(path).read_bytes())


def implementation_hash():
    files = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    return digest(canonical(files).encode())


def recipe_hash():
    root = recipe_root()
    paths = list((root / "src").rglob("*.py")) + list((root / "tests").rglob("*.py"))
    paths += list((root / "data").glob("*.jsonl"))
    paths += [root / name for name in ("README.md", "pyproject.toml", "requirements.txt", "Dockerfile", ".dockerignore", ".gitignore", ".env.example")]
    return digest(canonical({p.relative_to(root).as_posix(): file_hash(p) for p in sorted(paths) if p.is_file()}).encode())


def recipe_root():
    candidates = [Path(__file__).resolve().parents[2], Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "bodhan_eval").is_dir():
            return candidate
    raise ValueError("Run evidence tooling from the quality_harness recipe checkout")
