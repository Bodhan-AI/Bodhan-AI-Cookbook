"""Dataset loading.

Bad or partial items must not stop the run: they are logged, counted and
skipped, and the count appears in the summary. Audio paths resolve relative to
the dataset file, which is how a dataset folder is normally laid out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .checks.base import ItemContext

REQUIRED = ("id", "source", "lang")


@dataclass
class LoadResult:
    items: list[ItemContext] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def by_lang(self) -> dict[str, list[ItemContext]]:
        out: dict[str, list[ItemContext]] = {}
        for it in self.items:
            out.setdefault(it.lang, []).append(it)
        return out


def load_dataset(path: Path) -> LoadResult:
    path = Path(path)
    base = path.parent
    result = LoadResult()
    seen: set[str] = set()

    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                result.skipped.append({"line": lineno, "reason": f"invalid JSON: {exc.msg}"})
                continue

            missing = [k for k in REQUIRED if not obj.get(k)]
            if missing:
                result.skipped.append({"line": lineno, "reason": f"missing fields: {missing}"})
                continue
            if obj["id"] in seen:
                result.skipped.append({"line": lineno, "reason": f"duplicate id {obj['id']}"})
                continue
            seen.add(obj["id"])

            audio = obj.get("audio")
            if audio and not Path(audio).is_absolute():
                candidate = base / audio
                audio = str(candidate) if candidate.exists() else None

            result.items.append(ItemContext(
                id=str(obj["id"]),
                source=str(obj["source"]),
                lang=str(obj["lang"]),
                translation=obj.get("translation"),
                reference=obj.get("reference"),
                audio=audio,
                labels=obj.get("labels") or [],
                extra={k: v for k, v in obj.items()
                       if k not in {"id", "source", "lang", "translation",
                                    "reference", "audio", "labels"}},
            ))
    return result
