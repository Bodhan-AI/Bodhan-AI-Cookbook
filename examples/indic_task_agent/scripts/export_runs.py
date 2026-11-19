"""Copy finished suite runs into evaluation/runs/ for the repository, without media.

    python scripts/export_runs.py outputs/final [--dest evaluation/runs] [--media-out outputs/media_upload] [--media-base-url URL]

Text outputs (result.md, report.json, trace.jsonl, .md/.srt/.txt/.json artifacts) are
copied so reviewers can read them on GitHub. Audio and video stay out of git: they are
copied to --media-out for upload elsewhere, and MEDIA.md in each run lists them with
their full SHA-256 (and their public URL when --media-base-url is given).

Local absolute paths are rewritten: the run folder becomes ".", the recipe "<recipe>",
every input folder "<input>", and any other absolute path "<local-path>". The export
fails if anything that looks like a local path or a secret survives.

Safety: the source, destination and media folders must not overlap; nothing is deleted
until the new export has been written to a staging folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".srt", ".txt", ".json", ".jsonl"}
MEDIA_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4"}
BS = chr(92)  # backslash
ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)|(?<![\w.])/(?:home|Users|tmp|var|mnt|private|root)/)[^\s\"'<>|]*")
SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}")


class ExportError(Exception):
    pass


def overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def input_roots(run: Path) -> list[Path]:
    try:
        report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted({Path(f).parent for f in report.get("inputs", {}).get("files", [])}, key=lambda p: -len(str(p)))


def scrub(text: str, run_dir: Path, inputs: list[Path]) -> str:
    """Replace local absolute paths (plain, JSON-escaped or forward-slash) with markers."""
    for base, repl in [(run_dir.resolve(), "."), (ROOT.resolve(), "<recipe>")] + [(p, "<input>") for p in inputs]:
        b = str(base)
        for variant in (b.replace(BS, BS + BS), b, b.replace(BS, "/")):
            text = text.replace(variant, repl)
    return ABS_PATH.sub("<local-path>", text)


def leaks(text: str) -> list[str]:
    return [m.group(0)[:40] for m in ABS_PATH.finditer(text)] + [m.group(0)[:8] + "…" for m in SECRET.finditer(text)]


def export(src: Path, dest: Path, media_out: Path, media_base_url: str | None = None) -> list[str]:
    src, dest, media_out = src.resolve(), dest.resolve(), media_out.resolve()
    for a, b, what in ((src, dest, "source and destination"), (src, media_out, "source and media folder"), (dest, media_out, "destination and media folder")):
        if overlaps(a, b):
            raise ExportError(f"{what} overlap ({a} / {b}); choose separate folders. Nothing was changed.")
    runs = sorted(p for p in src.iterdir() if (p / "report.json").exists())
    if not runs:
        raise ExportError(f"No runs with report.json in {src}. Nothing was changed.")
    staging = dest.with_name(f"{dest.name}.staging-{os.getpid()}")
    if overlaps(staging, src) or overlaps(staging, media_out):  # the derived folder is checked like the given ones
        raise ExportError(f"the staging folder {staging} would overlap the source or media folder. Nothing was changed.")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    media_out.mkdir(parents=True, exist_ok=True)
    log = []
    for run in runs:
        inputs = input_roots(run)
        target = staging / run.name
        rows = []
        for f in sorted(run.rglob("*")):
            rel = f.relative_to(run)
            if not f.is_file() or any(part.startswith(".") for part in rel.parts):  # .state, .previous_runs: private
                continue
            if f.suffix.lower() in MEDIA_SUFFIXES:
                name = "__".join([run.name, *rel.parts])  # unique even when two folders hold the same file name
                shutil.copy2(f, media_out / name)
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                link = f"[{name}]({media_base_url.rstrip('/')}/{name})" if media_base_url else name
                rows.append(f"| `{rel.as_posix()}` | {f.stat().st_size / 1024:.0f} KB | `{digest}` | {link} |")
            elif f.suffix.lower() in TEXT_SUFFIXES:
                text = scrub(f.read_text(encoding="utf-8"), run, inputs)
                bad = leaks(text)
                if bad:
                    raise ExportError(f"{run.name}/{rel}: possible private path or secret left after scrubbing: {bad[:3]}")
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
        if rows:
            (target / "MEDIA.md").write_text(
                "# Media files from this run (not stored in git)\n\nGenerated audio is AI-generated speech (Bodhan Indic-Speak).\n\n"
                "| Artifact | Size | SHA-256 | File |\n|---|---|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")
        log.append(f"exported {run.name}: {len(rows)} media file(s) kept out of git")
    for extra in ("summary.json", "summary.md"):
        if (src / extra).exists():
            text = scrub((src / extra).read_text(encoding="utf-8"), src, [])
            bad = leaks(text)
            if bad:
                raise ExportError(f"{extra}: possible private path or secret left after scrubbing: {bad[:3]}")
            (staging / extra).write_text(text, encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)
    return log


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite_dir")
    ap.add_argument("--dest", default=str(ROOT / "evaluation" / "runs"))
    ap.add_argument("--media-out", default=str(ROOT / "outputs" / "media_upload"))
    ap.add_argument("--media-base-url", default=None, help="Public URL prefix where the media files will be hosted.")
    a = ap.parse_args()
    try:
        for line in export(Path(a.suite_dir), Path(a.dest), Path(a.media_out), a.media_base_url):
            print(line)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
