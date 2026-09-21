"""Validate one recipe directory against the cookbook rules.

    python scripts/validate_recipe.py examples/my_recipe
    python scripts/validate_recipe.py getting-started/translate --no-env-example

Exit status 1 when any blocking finding is reported.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bodhan_checks import has_errors, run_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recipe", nargs="+", help="recipe directory, e.g. examples/my_recipe")
    parser.add_argument("--no-env-example", action="store_true", help="skip the .env.example / .gitignore checks (getting-started tutorials)")
    args = parser.parse_args(argv)

    exit_code = 0
    for recipe in args.recipe:
        root = Path(recipe).resolve()
        if not root.is_dir():
            print(f"{recipe}: not a directory", file=sys.stderr)
            exit_code = 1
            continue
        findings = run_all(root, require_env_example=not args.no_env_example)
        print(f"== {recipe} ==")
        if not findings:
            print("   OK — no findings")
        for finding in findings:
            print(f"   {finding}")
        if has_errors(findings):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
