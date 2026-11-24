"""Ground rules checked in CI: no non-Bodhan ASR/MT/TTS/OCR anywhere, no PDF text-layer reads,
no secrets, and the vendored model rules match the Cookbook's copy."""

import ast
import json
import re
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[1]
SRC = RECIPE / "src"

BANNED_MODULES = {"whisper", "faster_whisper", "whisperx", "pytesseract", "tesserocr", "easyocr", "paddleocr",
                  "doctr", "indictrans", "IndicTransToolkit", "googletrans", "deep_translator", "argostranslate",
                  "translate", "gtts", "pyttsx3", "TTS", "speech_recognition", "vosk", "transformers", "torch",
                  "azure", "boto3", "google"}
BANNED_STRINGS = re.compile(r"whisper|tesseract|get_textpage|get_text_range|extract_text", re.I)
BANNED_PACKAGES = re.compile(r"^(openai-whisper|faster-whisper|whisperx|pytesseract|easyocr|paddleocr|indictrans|"
                             r"googletrans|deep-translator|argostranslate|gtts|pyttsx3|speechrecognition|vosk|"
                             r"transformers|torch|google-cloud|azure-cognitiveservices|boto3)\b", re.I | re.M)


def _python_files():
    return sorted(SRC.rglob("*.py"))


def test_no_banned_imports():
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in BANNED_MODULES, f"{path.name} imports {name}"


def test_no_banned_calls_or_ffmpeg_filters_in_code():
    """String constants and attribute names (docstrings/comments excluded) must not use ASR filters or PDF text."""
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body and \
                    isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert not BANNED_STRINGS.search(node.value), f"{path.name}: {node.value[:60]!r}"
            if isinstance(node, ast.Attribute):
                assert not BANNED_STRINGS.search(node.attr), f"{path.name}: .{node.attr}"


def test_no_banned_dependencies():
    for name in ("requirements.txt", "pyproject.toml"):
        assert not BANNED_PACKAGES.search((RECIPE / name).read_text(encoding="utf-8")), name


def test_no_secrets_in_tracked_text_files():
    key = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
    for path in RECIPE.rglob("*"):
        if any(part in (".venv", ".git", "__pycache__", "out", "outputs", "sample_data", ".cache") or
               part.startswith(".pytest") for part in path.parts) or path.name == ".env" or not path.is_file():
            continue
        if path.suffix in {".py", ".md", ".toml", ".txt", ".json", ".jsonl", ".example", ".yml", ".yaml"}:
            assert not key.search(path.read_text(encoding="utf-8", errors="ignore")), path


def test_vendored_rules_match_cookbook():
    cookbook = RECIPE.parents[1] / "scripts" / "bodhan_api_rules.json"
    if not cookbook.exists():
        pytest.skip("not inside the Cookbook checkout")
    vendored = RECIPE / "src" / "bodhan_mcp" / "data" / "bodhan_api_rules.json"
    assert json.loads(vendored.read_text(encoding="utf-8")) == json.loads(cookbook.read_text(encoding="utf-8"))
