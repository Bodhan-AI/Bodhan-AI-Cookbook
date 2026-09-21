"""Load and sanity-check the Bodhan model allowlist (scripts/bodhan_api_rules.json).

The JSON file is the single source of truth for which model ids, endpoints,
language codes and voices recipes may use. It is hand-maintained from
https://console.bodhan.ai/api-docs/ — when the API changes, update the JSON
and bump its ``version`` field.

Usage:
    python scripts/bodhan_rules.py --check     # validate the file, exit 1 on problems
    python scripts/bodhan_rules.py --print     # dump a human-readable summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent / "bodhan_api_rules.json"

REQUIRED_MODEL_KEYS = {"display_name", "capability", "endpoint", "content_type", "openai_compatible"}


def load_rules(path: Path = RULES_PATH) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def allowed_models(rules: dict) -> set[str]:
    return set(rules["models"])


def deprecated_models(rules: dict) -> dict[str, str]:
    return dict(rules.get("deprecated_models", {}))


def check_rules(rules: dict) -> list[str]:
    """Return a list of problems; empty means the file is consistent."""
    problems: list[str] = []
    for key in ("version", "source", "base_url", "auth", "models"):
        if key not in rules:
            problems.append(f"missing top-level key: {key}")
    if problems:
        return problems

    if not rules["base_url"].startswith("https://"):
        problems.append("base_url must be https")

    names = rules.get("language_names", {})
    for model_id, spec in rules["models"].items():
        missing = REQUIRED_MODEL_KEYS - set(spec)
        if missing:
            problems.append(f"{model_id}: missing keys {sorted(missing)}")
        if model_id != model_id.lower():
            problems.append(f"{model_id}: model ids are lower-case (the API is case-sensitive)")
        for code in spec.get("languages", []):
            if code not in names:
                problems.append(f"{model_id}: language code {code!r} has no entry in language_names")
        for code in spec.get("voices", {}):
            if code not in spec.get("languages", []):
                problems.append(f"{model_id}: voice language {code!r} is not in its languages list")

    for old, new in deprecated_models(rules).items():
        if new not in rules["models"]:
            problems.append(f"deprecated model {old} points at unknown replacement {new}")
        if old in rules["models"]:
            problems.append(f"{old} is listed as both current and deprecated")
    return problems


def summary(rules: dict) -> str:
    lines = [f"Bodhan API rules v{rules['version']} — {rules['base_url']}", ""]
    for model_id, spec in rules["models"].items():
        lines.append(f"{model_id:22} {spec['capability']:18} {spec['endpoint']}")
        if "languages" in spec:
            lines.append(f"{'':22} {len(spec['languages'])} languages")
        if "voices" in spec:
            count = sum(len(v) for lang in spec["voices"].values() for v in lang.values())
            lines.append(f"{'':22} {count} voices")
    if rules.get("deprecated_models"):
        lines.append("")
        lines.append("Deprecated ids: " + ", ".join(f"{k} -> {v}" for k, v in rules["deprecated_models"].items()))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate the rules file")
    parser.add_argument("--print", action="store_true", help="print a summary")
    args = parser.parse_args(argv)

    rules = load_rules()
    if args.print or not args.check:
        print(summary(rules))
    if args.check:
        problems = check_rules(rules)
        if problems:
            print("bodhan_api_rules.json has problems:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"bodhan_api_rules.json OK (v{rules['version']}, {len(rules['models'])} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
