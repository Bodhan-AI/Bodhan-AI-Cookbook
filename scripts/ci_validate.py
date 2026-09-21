"""Validate every recipe touched relative to a base git ref (used by CI).

    python scripts/ci_validate.py --base-ref main
    python scripts/ci_validate.py --all

Recipes live under examples/, getting-started/ and integrations/. A recipe is
"touched" when any file inside its top-level directory changed. Repo-wide
security scanning (secrets, committed .env files) always runs over the whole
tree, regardless of the diff.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bodhan_checks import check_security, has_errors, run_all  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECIPE_PARENTS = ("examples", "getting-started", "integrations")
# getting-started tutorials are single notebooks, not apps; they read the key
# from the environment and do not ship .env.example/.gitignore.
NO_ENV_EXAMPLE = ("getting-started", "integrations")


def changed_paths(base_ref: str) -> list[str]:
    cmd = ["git", "diff", "--name-only", f"{base_ref}...HEAD"]
    try:
        out = subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        # Shallow clone or unknown ref — fall back to a two-dot diff.
        out = subprocess.run(["git", "diff", "--name-only", base_ref], cwd=REPO, check=True, capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def recipe_dirs_from_paths(paths: list[str]) -> list[Path]:
    dirs: set[Path] = set()
    for p in paths:
        parts = Path(p).parts
        if len(parts) >= 2 and parts[0] in RECIPE_PARENTS:
            candidate = REPO / parts[0] / parts[1]
            if candidate.is_dir() and candidate.name != "TEMPLATE":  # the template keeps its placeholders on purpose
                dirs.add(candidate)
    return sorted(dirs)


def all_recipe_dirs() -> list[Path]:
    dirs: list[Path] = []
    for parent in RECIPE_PARENTS:
        base = REPO / parent
        if base.is_dir():
            dirs += sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "TEMPLATE")
    return dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--all", action="store_true", help="validate every recipe, not just changed ones")
    args = parser.parse_args(argv)

    recipes = all_recipe_dirs() if args.all else recipe_dirs_from_paths(changed_paths(args.base_ref))
    failed = False

    print("== repo-wide secret scan ==")
    repo_findings = list(check_security(REPO))
    for f in repo_findings:
        print(f"   {f}")
    if not repo_findings:
        print("   OK")
    failed |= has_errors(repo_findings)

    if not recipes:
        print("\nNo recipe directories changed — nothing else to validate.")
        return 1 if failed else 0

    for root in recipes:
        rel = root.relative_to(REPO)
        require_env = rel.parts[0] not in NO_ENV_EXAMPLE
        findings = run_all(root, require_env_example=require_env)
        print(f"\n== {rel} ==")
        if not findings:
            print("   OK — no findings")
        for f in findings:
            print(f"   {f}")
        failed |= has_errors(findings)

    print()
    print("FAILED — fix the [ERROR] findings above" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
