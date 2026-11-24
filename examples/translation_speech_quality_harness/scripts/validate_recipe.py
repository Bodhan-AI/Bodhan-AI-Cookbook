#!/usr/bin/env python3
"""Validate the submission contract for this recipe without reading credentials."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

RECIPE_ROOT = Path(__file__).resolve().parents[1]
PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME)\b|\{\{[^}]*\}\}|\[insert\b", re.IGNORECASE)
MODEL_ID = re.compile(r"\b(?:indic|bodhan)-[a-z0-9][a-z0-9-]*\b", re.IGNORECASE)
SECRET = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"ghp_[A-Za-z0-9]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)
URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)", re.IGNORECASE)
ALLOWED_MODEL_IDS = {
    "indic-ocr",
    "indic-speak",
    "indic-transcribe",
    "indic-translate",
    "indic-transliterate",
}
ALLOWED_SERVICE_HOSTS = {"api.bodhan.ai"}


def _read(path: Path, problems: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"cannot read {path.name}: {error}")
        return ""


def _source_files(recipe_dir: Path) -> list[Path]:
    roots = [recipe_dir / "src", recipe_dir / "scripts"]
    return sorted(
        path
        for root in roots
        if root.is_dir()
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def validate_recipe(recipe_dir: Path = RECIPE_ROOT) -> list[str]:
    """Return all submission-contract violations found in a recipe directory."""
    root = recipe_dir.resolve()
    problems: list[str] = []
    required = ("README.md", "requirements.txt", ".env.example", ".gitignore", "src/bodhan_eval")
    for relative in required:
        path = root / relative
        if not path.exists():
            problems.append(f"missing required recipe path: {relative}")

    readme = _read(root / "README.md", problems)
    if "## Setup" not in readme:
        problems.append("README.md must contain ## Setup")
    if "## Run" not in readme:
        problems.append("README.md must contain ## Run")
    if PLACEHOLDER.search(readme):
        problems.append("README.md contains a template placeholder")

    requirements_path = root / "requirements.txt"
    requirements = _read(requirements_path, problems)
    invalid_requirements = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not PINNED_REQUIREMENT.fullmatch(line.strip())
    ]
    if invalid_requirements:
        problems.append("requirements.txt must pin every dependency as name==version")

    env_example_path = root / ".env.example"
    env_example = _read(env_example_path, problems)
    if "BODHAN_API_KEY" not in env_example:
        problems.append(".env.example must contain the literal BODHAN_API_KEY")
    if not any(
        line.lstrip().startswith("#") and re.search(r"per[- ]model keys", line, re.IGNORECASE)
        for line in env_example.splitlines()
    ):
        problems.append(".env.example must include a comment that per-model keys are used instead")

    ignore_path = root / ".gitignore"
    ignore_lines = {
        line.strip().rstrip("/")
        for line in _read(ignore_path, problems).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if ".env" not in ignore_lines:
        problems.append(".gitignore must ignore .env")
    if not {".cache", "cache"} & ignore_lines:
        problems.append(".gitignore must ignore a cache directory")
    if not {"reports", "outputs", "report"} & ignore_lines:
        problems.append(".gitignore must ignore report output")
    if not {"*.wav", "audio", "generated_audio"} & ignore_lines:
        problems.append(".gitignore must ignore generated audio")

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            problems.append(f"unexpected .env file: {path.relative_to(root)}")

    for path in _source_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(f"cannot read source file {path.relative_to(root)}: {error}")
            continue
        relative = path.relative_to(root).as_posix()
        for match in MODEL_ID.finditer(text):
            if match.group(0).lower() not in ALLOWED_MODEL_IDS:
                problems.append(f"unapproved model id in {relative}: {match.group(0)}")
        if SECRET.search(text):
            problems.append(f"secret-shaped literal in {relative}")
        for match in URL_HOST.finditer(text):
            if match.group(1).lower() not in ALLOWED_SERVICE_HOSTS:
                problems.append(f"non-Bodhan service URL in {relative}: {match.group(1)}")

    return sorted(set(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe_dir", nargs="?", type=Path, default=RECIPE_ROOT)
    args = parser.parse_args(argv)
    problems = validate_recipe(args.recipe_dir)
    if problems:
        print("Recipe validation failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Recipe validation passed: {args.recipe_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
