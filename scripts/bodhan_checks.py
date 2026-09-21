"""Recipe checks shared by validate_recipe.py and ci_validate.py.

Three families of checks run over a recipe directory (``examples/<name>/``,
``getting-started/<capability>/`` or ``integrations/<name>/``):

* **security** — no committed secrets, no ``.env`` files, no real keys in
  notebooks or source. Findings here are always blocking.
* **structure** — the files every recipe must ship (README, pinned
  requirements, ``.env.example``, ``.gitignore``, cleared notebook outputs).
* **api** — model ids and language codes must match
  ``scripts/bodhan_api_rules.json``; deprecated ids are rejected.

Each check yields ``Finding`` objects with a ``level`` of ``error`` (blocks
the PR) or ``warning`` (reported, does not block).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from bodhan_rules import allowed_models, deprecated_models, load_rules

SOURCE_SUFFIXES = {".py", ".ipynb", ".js", ".ts", ".tsx", ".jsx", ".sh", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".env", ".example"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".ipynb_checkpoints", "outputs", "sample_data", ".next", "dist", "build"}
MAX_SCAN_BYTES = 2_000_000

# Patterns that indicate a real credential rather than a placeholder.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Bodhan/OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]
# `BODHAN_API_KEY = "something-that-is-not-a-placeholder"`
ASSIGNED_KEY = re.compile(r"""BODHAN_API_KEY\s*[:=]\s*["']?([^"'\s<>${}]{16,})["']?""")
PLACEHOLDER_HINTS = ("your", "xxx", "placeholder", "example", "replace", "<", "$", "todo", "changeme", "dummy")

MODEL_LITERAL = re.compile(
    r"""["'](?:model|model_id)["']?\s*[:=]\s*["']([A-Za-z0-9._/-]+)["']"""  # "model": "x" / model="x" in JSON/dicts
    r"""|-F\s+["']?model=([A-Za-z0-9._/-]+)"""  # curl -F model=x
    r"""|model\s*=\s*["']([A-Za-z0-9._/-]+)["']"""  # model="x" kwargs
)
BCP47_IN = re.compile(r"""["'](?:[a-z]{2,3})-IN["']""")
NON_BODHAN_BASE = re.compile(r"https://api\.(?:openai|sarvam|anthropic|groq|together)\.[a-z.]+")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    check: str  # "security" | "structure" | "api"
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper():7}] {self.check:9} {self.path}: {self.message}"


def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def _read_text(path: Path) -> str | None:
    if path.stat().st_size > MAX_SCAN_BYTES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root.parent)) if root.parent != path else str(path)


# ----------------------------------------------------------------------------
# security
# ----------------------------------------------------------------------------

def check_security(root: Path) -> Iterator[Finding]:
    for path in _iter_files(root):
        rel = _rel(root, path)
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            yield Finding("error", "security", rel, "environment file committed — delete it and rotate any key inside")
            continue
        if path.suffix not in SOURCE_SUFFIXES and path.name not in {".env.example", ".gitignore"}:
            continue
        text = _read_text(path)
        if text is None:
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                yield Finding("error", "security", rel, f"looks like a real {label}: {match.group(0)[:12]}…")
        for match in ASSIGNED_KEY.finditer(text):
            value = match.group(1)
            if not any(hint in value.lower() for hint in PLACEHOLDER_HINTS):
                yield Finding("error", "security", rel, "BODHAN_API_KEY is assigned a literal value — read it from the environment instead")


# ----------------------------------------------------------------------------
# structure
# ----------------------------------------------------------------------------

SNAKE_CASE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def _notebook_has_outputs(path: Path) -> bool | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        nb = json.loads(text)
    except json.JSONDecodeError:
        return None
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code" and (cell.get("outputs") or cell.get("execution_count") is not None):
            return True
    return False


def check_structure(root: Path, *, require_env_example: bool = True) -> Iterator[Finding]:
    rel_root = str(root)
    readme = root / "README.md"
    if not readme.exists():
        yield Finding("error", "structure", rel_root, "README.md is missing")
    else:
        text = _read_text(readme) or ""
        for heading in ("## Setup", "## Run"):
            if heading.lower() not in text.lower():
                yield Finding("warning", "structure", str(readme), f"README has no '{heading}' section")
        if "<one-line description" in text or "<Recipe title>" in text:
            yield Finding("error", "structure", str(readme), "README still contains template placeholders")

    code_files = [p for p in _iter_files(root) if p.suffix in {".ipynb", ".py", ".js", ".ts", ".tsx"}]
    if not code_files:
        yield Finding("error", "structure", rel_root, "no notebook or source file found (.ipynb/.py/.js/.ts)")

    py_recipe = any(p.suffix in {".ipynb", ".py"} for p in code_files)
    js_recipe = any(p.suffix in {".js", ".ts", ".tsx"} for p in code_files)
    if py_recipe:
        req = root / "requirements.txt"
        if not req.exists():
            yield Finding("error", "structure", rel_root, "requirements.txt is missing")
        else:
            for line in (_read_text(req) or "").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                if "==" not in line:
                    yield Finding("error", "structure", str(req), f"unpinned dependency: {line!r} (use name==version)")
    if js_recipe and not (root / "package.json").exists():
        yield Finding("error", "structure", rel_root, "package.json is missing for a JS/TS recipe")

    if require_env_example:
        env_example = root / ".env.example"
        if not env_example.exists():
            yield Finding("error", "structure", rel_root, ".env.example is missing")
        elif "BODHAN_API_KEY" not in (_read_text(env_example) or ""):
            yield Finding("error", "structure", str(env_example), ".env.example must declare BODHAN_API_KEY")

        gitignore = root / ".gitignore"
        if not gitignore.exists():
            yield Finding("error", "structure", rel_root, ".gitignore is missing (must ignore .env)")
        elif ".env" not in (_read_text(gitignore) or ""):
            yield Finding("error", "structure", str(gitignore), ".gitignore does not ignore .env")

    for nb in (p for p in code_files if p.suffix == ".ipynb"):
        if not SNAKE_CASE.match(nb.stem) and nb.stem != "TEMPLATE":
            yield Finding("warning", "structure", str(nb), "notebook name should be snake_case")
        has_outputs = _notebook_has_outputs(nb)
        if has_outputs is None:
            yield Finding("error", "structure", str(nb), "notebook is not valid JSON")
        elif has_outputs:
            yield Finding("error", "structure", str(nb), "notebook has outputs — run `make clean-outputs` before committing")


# ----------------------------------------------------------------------------
# api compliance
# ----------------------------------------------------------------------------

def _model_literals(text: str) -> Iterable[str]:
    for match in MODEL_LITERAL.finditer(text):
        value = next(g for g in match.groups() if g)
        yield value


def check_api(root: Path, rules: dict | None = None) -> Iterator[Finding]:
    rules = rules or load_rules()
    allowed = allowed_models(rules)
    deprecated = deprecated_models(rules)
    for path in _iter_files(root):
        if path.suffix not in {".py", ".ipynb", ".js", ".ts", ".tsx", ".sh", ".md"}:
            continue
        text = _read_text(path)
        if text is None:
            continue
        rel = _rel(root, path)
        seen: set[str] = set()
        for model in _model_literals(text):
            if model in seen:
                continue
            seen.add(model)
            if model in deprecated:
                yield Finding("error", "api", rel, f"model {model!r} is deprecated — use {deprecated[model]!r}")
            elif model in allowed:
                continue
            elif model.startswith(("indic-", "bodhan-")):
                yield Finding("error", "api", rel, f"model {model!r} is not in scripts/bodhan_api_rules.json")
            # Other providers' models (e.g. an LLM used alongside Bodhan) are allowed silently.
        if BCP47_IN.search(text):
            yield Finding("warning", "api", rel, "Bodhan uses ISO codes like 'hi', not 'hi-IN'")
        for match in NON_BODHAN_BASE.finditer(text):
            yield Finding(
                "warning", "api", rel,
                f"non-Bodhan base URL {match.group(0)} — fine for a companion model, but Bodhan calls must go to {rules['base_url']}",
            )


# ----------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------

def run_all(root: Path, *, require_env_example: bool = True, rules: dict | None = None) -> list[Finding]:
    findings = list(check_security(root))
    findings += list(check_structure(root, require_env_example=require_env_example))
    findings += list(check_api(root, rules))
    return findings


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
