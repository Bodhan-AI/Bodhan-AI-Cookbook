"""Checks applied in code to every planner-issued tool call.

The rules they enforce are not left to the prompt:
- Indian-language text is only ever produced by Bodhan models. Any free-text
  argument the planner writes (inline `text`, answers, file contents) must be
  free of Indic script; Indic content moves between tools as file paths.
- Tools may only touch the run's input files and files produced during the run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bodhan_agent.trace import is_path_key
from bodhan_mcp.languages import contains_indic, lang_name, normalize_lang

INDIC_TEXT_MESSAGE = (
    "Argument {keys} contains Indian-language script. Indian-language text must come from a Bodhan model, "
    "never from the planner. Pass `path` pointing at the file a Bodhan tool produced (transcript_path, "
    "output_path, markdown_path, ...) or at an input file instead of inline text."
)


@dataclass
class GuardViolation:
    code: str
    message: str
    hint: str = ""

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint, "retryable": False}


def canonical(path: str | os.PathLike[str]) -> str:
    """Stable key for comparing paths (resolves `..`, symlinks and Windows case)."""
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def indic_text_keys(value: Any, key: str = "") -> list[str]:
    """Names of free-text arguments (anything that is not a path) containing Indic script."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(indic_text_keys(v, str(k)))
    elif isinstance(value, list):
        for v in value:
            found.extend(indic_text_keys(v, key))
    elif isinstance(value, str) and not is_path_key(key) and contains_indic(value):
        found.append(key or "value")
    return sorted(set(found))


def check_english_only(text: str) -> GuardViolation | None:
    if contains_indic(text):
        return GuardViolation(
            "indic_text_not_allowed",
            "Text written by the planner must be English (no Indian-language script).",
            "Write the English version, save it, then use translate_text(path=...) to get the target language.",
        )
    return None


def path_values(args: dict[str, Any]) -> list[str]:
    """Every string under a path-like key, including lists of paths."""
    out: list[str] = []
    for k, v in args.items():
        if not is_path_key(str(k)) or k == "artifacts":
            continue
        if isinstance(v, str) and v.strip():
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str) and x.strip())
    return out


@dataclass
class PathPolicy:
    """Inputs (files or directories) plus the run's working directory."""

    roots: list[Path] = field(default_factory=list)
    extra_files: set[str] = field(default_factory=set)

    def add_root(self, path: Path) -> None:
        self.roots.append(Path(path).expanduser().resolve())

    def add_file(self, path: str) -> None:
        self.extra_files.add(canonical(path))

    def allows(self, path: str) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return False
        if canonical(resolved) in self.extra_files:
            return True
        key = canonical(resolved)
        for root in self.roots:
            root_key = canonical(root)
            if key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep):
                return True
        return False


def _base_lang(code: str | None) -> str | None:
    norm = normalize_lang(code) if code else None
    return norm.split("-")[0] if norm else None


@dataclass
class CallGuard:
    policy: PathPolicy
    # Language of text files whose language is known for sure (planner-written files are English).
    known_text_language: dict[str, str] = field(default_factory=dict)

    def check(self, name: str, args: dict[str, Any]) -> GuardViolation | None:
        keys = indic_text_keys(args)
        if keys:
            return GuardViolation(
                "indic_text_not_allowed",
                INDIC_TEXT_MESSAGE.format(keys=", ".join(f"`{k}`" for k in keys)),
                "Use the path returned by the tool that produced this text.",
            )
        for p in path_values(args):
            if not self.policy.allows(p):
                return GuardViolation(
                    "path_not_allowed",
                    f"Path {p!r} is outside this run's inputs and working directory.",
                    "Only the input files listed in the inventory and files produced during this run can be used.",
                )
        if name == "synthesize_speech":
            return self._check_speech_language(args)
        return None

    def _check_speech_language(self, args: dict[str, Any]) -> GuardViolation | None:
        """Catch reading English text with an Indic language setting (or the reverse)."""
        target = _base_lang(args.get("language"))
        if not target:
            return None
        source: str | None = None
        if isinstance(args.get("text"), str) and args["text"].strip():
            source = "en"  # inline text already passed the no-Indic check
        elif isinstance(args.get("path"), str):
            source = _base_lang(self.known_text_language.get(canonical(args["path"])))
        if source and source != target and "en" in (source, target):
            return GuardViolation(
                "language_mismatch",
                f"The text is {lang_name(source)} but language={args.get('language')!r} was requested. "
                "Indic-Speak reads text as-is; it does not translate.",
                f"First translate_text(path=..., target_language={target!r}) and synthesize its output_path, "
                f"or set language={source!r} if {lang_name(source)} speech is what you want.",
            )
        return None
