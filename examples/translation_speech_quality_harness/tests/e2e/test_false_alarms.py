"""Clean-item false-alarm suite (TESTING.md §6; Phase 2 gate).

Twenty clean but tricky items in hi/bn/ta, run through the exact command against the stub: numbers in
words and words in digits, Indic digits and Indian grouping, transliterated and Latin-kept names,
acronyms and formulas, reordered / merged sentences, a translated table with translated ``\\text{}``,
Markdown lists with code and links, units spelled out, a reordered date, a paraphrased back-translation.

Gate: zero flags involving a tier-0 check, and at most 10 % of clean items flagged overall.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bodhan_eval.testing.stub_server import StubServer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "clean"
TIER0 = {"script", "format", "length", "reference", "numbers"}
MAX_FLAGGED_SHARE = 0.10


def _strings(obj: object) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in _strings(k) + _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_fixtures_have_no_stray_control_characters() -> None:
    """A lost backslash once turned '\\text' into TAB + 'ext' in a fixture; only newlines are legitimate."""
    root = FIXTURES.parent
    for path in sorted(root.rglob("*.json")) + sorted(root.rglob("*.jsonl")):
        lines = path.read_text("utf-8").splitlines() if path.suffix == ".jsonl" else [path.read_text("utf-8")]
        for line in lines:
            if line.startswith("{") and line.endswith("}") or path.suffix == ".json":
                for s in _strings(json.loads(line)):
                    bad = sorted({hex(ord(c)) for c in s if ord(c) < 32 and c != "\n"})
                    assert not bad, f"{path.name}: control characters {bad} in {s[:60]!r}"


def test_clean_items_raise_no_false_alarms(tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "dataset.jsonl", tmp_path / "clean.jsonl")
    with StubServer(FIXTURES / "stub.json") as stub:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BODHAN_", "LLM_"))} | stub.env()
        cmd = [
            sys.executable, "-m", "bodhan_eval", "--dataset", "clean.jsonl", "--langs", "hi,bn,ta", "--out", "report",
            "--no-speech", "--cache-dir", "cache", "--log-level", "warning",
        ]  # fmt: skip
        proc = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr
    items = [json.loads(x) for x in (tmp_path / "report" / "items.jsonl").read_text("utf-8").splitlines()]
    assert len(items) == 20 and all(it["status"] == "ok" for it in items)

    def describe(it: dict, f: dict) -> str:
        return f"{it['id']}: {f['category']} {f['confidence']} via {f['checks']}: {f['evidence'][0]['detail'][:100]}"

    tier0 = [describe(it, f) for it in items for f in it["flags"] + it["uncertain"] if TIER0 & set(f["checks"])]
    assert tier0 == [], "tier-0 false alarms:\n" + "\n".join(tier0)
    flagged = [describe(it, f) for it in items for f in it["flags"]]
    share = sum(bool(it["flags"]) for it in items) / len(items)
    assert share <= MAX_FLAGGED_SHARE, f"{share:.0%} of clean items flagged:\n" + "\n".join(flagged)
