"""Cassettes: real Bodhan results recorded once (evals/record_cassettes.py) and replayed by the stubs for free.

A cassette holds the CONTENT of real tool results, not file paths, so the stubs keep building paths and files for
whatever output locations the agent picks. Lookups are by what was asked, never by where the output goes:
  translate_text   -> (source text with whitespace collapsed, target language)
  ocr_document     -> input file name
  transcribe_audio -> input file name
"""

from __future__ import annotations

import json
from pathlib import Path

CASSETTE_DIR = Path(__file__).with_name("cassettes")
TOOLS = ("translate_text", "ocr_document", "transcribe_audio")


def norm(text: str) -> str:
    return " ".join(text.split())


def path_for(case_id: str) -> Path:
    return CASSETTE_DIR / f"{case_id}.json"


def load(case_id: str) -> dict | None:
    p = path_for(case_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(case_id: str, data: dict) -> None:
    CASSETTE_DIR.mkdir(exist_ok=True)
    path_for(case_id).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_translation(cassette: dict | None, source: str, target: str) -> dict | None:
    for e in (cassette or {}).get("translate_text", []):
        if e["target"] == target and norm(e["source"]) == norm(source):
            return e
    return None


def find_file(cassette: dict | None, tool: str, filename: str) -> dict | None:
    for e in (cassette or {}).get(tool, []):
        if e["file"] == filename:
            return e
    return None
