"""Copy finished suite runs into evaluation/runs/ for the repository, without media.

    python scripts/export_runs.py outputs/final [--media-out ../media_upload]

Text outputs (result.md, report.json, trace.jsonl, .md/.srt/.txt/.json artifacts)
are copied as they are, so reviewers can read them on GitHub. Audio and video stay
out of git: they are copied to --media-out for upload elsewhere, and a
MEDIA.md in each run lists them with their size and SHA-256 so a downloaded file can
be checked against the trace. Absolute local paths are rewritten to run-relative ones.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".srt", ".txt", ".json", ".jsonl"}
BS = chr(92)  # backslash
MEDIA_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4"}


def scrub(text: str, run_dir: Path) -> str:
    """Replace absolute local paths (plain, JSON-escaped or forward-slash) with relative markers."""
    for base, repl in ((run_dir.resolve(), "."), (ROOT.resolve(), "<recipe>")):
        b = str(base)
        for variant in (b.replace(BS, BS + BS), b, b.replace(BS, "/")):
            text = text.replace(variant, repl)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite_dir")
    ap.add_argument("--dest", default=str(ROOT / "evaluation" / "runs"))
    ap.add_argument("--media-out", default=str(ROOT / "outputs" / "media_upload"))
    a = ap.parse_args()
    src, dest, media_out = Path(a.suite_dir).resolve(), Path(a.dest), Path(a.media_out)
    media_out.mkdir(parents=True, exist_ok=True)
    for run in sorted(p for p in src.iterdir() if (p / "report.json").exists()):
        target = dest / run.name
        if target.exists():
            shutil.rmtree(target)
        media_rows = []
        for f in sorted(run.rglob("*")):
            if not f.is_file() or ".previous_runs" in f.parts:
                continue
            rel = f.relative_to(run)
            if f.suffix.lower() in MEDIA_SUFFIXES:
                name = f"{run.name}__{f.name}"
                shutil.copy2(f, media_out / name)
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                media_rows.append(f"| `{rel.as_posix()}` | {f.stat().st_size / 1024:.0f} KB | `{digest[:16]}…` | {name} |")
            elif f.suffix.lower() in TEXT_SUFFIXES:
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(scrub(f.read_text(encoding="utf-8"), run), encoding="utf-8")
        if media_rows:
            (target / "MEDIA.md").write_text(
                "# Media files from this run (not stored in git)\n\nGenerated audio is AI-generated speech (Bodhan Indic-Speak).\n\n"
                "| Artifact | Size | SHA-256 | Uploaded as |\n|---|---|---|---|\n" + "\n".join(media_rows) + "\n", encoding="utf-8")
        print(f"exported {run.name}: {len(media_rows)} media file(s) kept out of git")
    for extra in ("summary.json", "summary.md"):
        if (src / extra).exists():
            (dest / extra).write_text(scrub((src / extra).read_text(encoding="utf-8"), src), encoding="utf-8")


if __name__ == "__main__":
    main()
