"""Strip outputs and execution counts from every notebook in the repository.

    python scripts/clear_notebook_outputs.py            # whole repo
    python scripts/clear_notebook_outputs.py path/to.ipynb

Outputs can carry API keys, personal data and multi-megabyte audio; the CI
structure check rejects notebooks that still have them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {".git", "node_modules", ".venv", "venv", ".ipynb_checkpoints"}


def clear(path: Path) -> bool:
    nb = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        if cell.get("outputs") or cell.get("execution_count") is not None:
            cell["outputs"] = []
            cell["execution_count"] = None
            changed = True
    if changed:
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or [p for p in REPO.rglob("*.ipynb") if not SKIP & set(p.parts)]
    cleared = [p for p in targets if clear(p)]
    for p in cleared:
        print(f"cleared {p.relative_to(REPO) if p.is_relative_to(REPO) else p}")
    print(f"{len(cleared)} notebook(s) changed, {len(targets) - len(cleared)} already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
