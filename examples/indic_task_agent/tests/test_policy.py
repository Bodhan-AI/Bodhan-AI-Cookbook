"""Repo-level rules: Bodhan-only models, no secrets in git, Indic text travels as paths."""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BANNED = ("whisper", "tesseract", "pytesseract", "indictrans", "google.cloud", "speech_recognition",
          "azure.cognitiveservices", "elevenlabs", "polly")
SECRET_SHAPES = re.compile(r"sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def test_no_other_provider_is_referenced_in_src():
    hits = []
    for path in (ROOT / "src").rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        hits += [f"{path.relative_to(ROOT)}: {tok}" for tok in BANNED if tok in lowered]
    assert hits == []


def test_env_example_lists_every_variable_and_no_real_looking_value():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    for name in ("BODHAN_API_KEY", "BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY",
                 "BODHAN_OCR_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        assert name in values, name
    for name, value in values.items():
        assert not SECRET_SHAPES.search(value), name
        if name.endswith(("_KEY", "_TOKEN")):
            assert value.startswith("<"), f"{name} must be a placeholder"


def test_gitignore_ignores_env_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines and "!.env.example" in lines


def test_tests_contain_no_real_looking_secrets():
    for path in (ROOT / "tests").glob("*.py"):
        assert not SECRET_SHAPES.search(path.read_text(encoding="utf-8")), path.name


def _public_functions(package: str):
    pkg = importlib.import_module(package)
    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"{package}.{info.name}")
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__ and not name.startswith("_"):
                yield f"{mod.__name__}.{name}", fn


@pytest.mark.parametrize("package", ["bodhan_mcp.tools"])
def test_no_tool_takes_text_inline(package):
    seen = list(_public_functions(package))
    assert len(seen) >= 6
    for qualname, fn in seen:
        params = set(inspect.signature(fn).parameters)
        assert not params & {"text", "content", "body", "transcript"}, qualname


def test_server_tool_wrappers_take_no_inline_text():
    server = importlib.import_module("bodhan_mcp.server")
    wrappers = [fn for name, fn in inspect.getmembers(server, inspect.isfunction) if name.endswith("_tool")]
    assert len(wrappers) == 6
    for fn in wrappers:
        assert not set(inspect.signature(fn).parameters) & {"text", "content", "body", "transcript"}


def test_no_tracked_file_has_a_real_looking_secret():
    import subprocess

    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    bad = []
    for name in filter(None, files):
        path = ROOT / name
        if path.suffix in {".py", ".md", ".json", ".jsonl", ".txt", ".toml", ".yml", ".yaml", ".example", ""} and path.is_file():
            if SECRET_SHAPES.search(path.read_text(encoding="utf-8", errors="ignore")):
                bad.append(name)
    assert bad == []
