"""Read the inbox manifest: one row per piece of feedback, typed, spoken or photographed."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

CHANNELS = ("text", "voice", "card")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s-]?){9,12}\d(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@dataclass(frozen=True)
class Item:
    id: str
    channel: str
    text: str = ""
    file: Path | None = None
    language: str | None = None


def read_manifest(path: str | Path) -> list[Item]:
    path = Path(path)
    items: list[Item] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            item_id = (row.get("id") or "").strip()
            channel = (row.get("channel") or "").strip().lower()
            text = (row.get("text") or "").strip()
            file_name = (row.get("file") or "").strip()
            if not item_id or item_id in seen:
                raise ValueError(f"{path}:{line}: 'id' is empty or repeated")
            if channel not in CHANNELS:
                raise ValueError(f"{path}:{line}: channel must be one of {', '.join(CHANNELS)}")
            if channel == "text" and not text:
                raise ValueError(f"{path}:{line}: a text row needs 'text'")
            if channel != "text" and not file_name:
                raise ValueError(f"{path}:{line}: a {channel} row needs 'file'")
            seen.add(item_id)
            items.append(
                Item(
                    id=item_id,
                    channel=channel,
                    text=text,
                    file=(path.parent / file_name) if file_name else None,
                    language=(row.get("language") or "").strip() or None,
                )
            )
    return items


def mask_pii(text: str) -> str:
    return EMAIL.sub("[email]", PHONE.sub("[phone]", text))
