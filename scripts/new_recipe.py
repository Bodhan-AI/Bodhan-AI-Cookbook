"""Scaffold a new example recipe from examples/TEMPLATE.

    python scripts/new_recipe.py my_new_recipe

Creates examples/my_new_recipe/ with the template files, renames the notebook
to match the directory, and prints the next steps.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "examples" / "TEMPLATE"
SNAKE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    name = argv[0]
    if not SNAKE.match(name):
        print(f"recipe name must be snake_case, got {name!r}", file=sys.stderr)
        return 1
    dest = REPO / "examples" / name
    if dest.exists():
        print(f"{dest.relative_to(REPO)} already exists", file=sys.stderr)
        return 1
    shutil.copytree(TEMPLATE, dest)
    (dest / "TEMPLATE.ipynb").rename(dest / f"{name}.ipynb")
    readme = dest / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("TEMPLATE", name), encoding="utf-8")
    print(f"Created examples/{name}/")
    print("Next:")
    print(f"  1. Edit examples/{name}/README.md (title, description, setup, run)")
    print(f"  2. Build examples/{name}/{name}.ipynb — or replace it with app.py / a web app")
    print(f"  3. Pin dependencies in examples/{name}/requirements.txt")
    print(f"  4. python scripts/validate_recipe.py examples/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
