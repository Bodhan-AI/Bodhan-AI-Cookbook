"""Auto-fail guards: only Bodhan models may do OCR, translation, TTS and ASR."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED = ("pytesseract", "easyocr", "paddleocr", "paddlepaddle", "googletrans", "deep_translator", "deep-translator",
          "indictrans", "gtts", "pyttsx3", "whisper", "faster_whisper", "speech_recognition", "boto3",
          "azure.cognitiveservices", "google.cloud", "transformers", "torch", "openai")


def _imports(text: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"^\s*(?:from|import)\s+([\w.\-]+)", text, re.MULTILINE)}


def test_source_imports_no_banned_package():
    for path in (ROOT / "src").rglob("*.py"):
        found = {i for i in _imports(path.read_text(encoding="utf-8"))
                 if any(i == b or i.startswith(b + ".") for b in BANNED)}
        assert not found, f"{path} imports banned package(s): {found}"


def test_dependency_files_list_no_banned_package():
    for name in ("requirements.txt", "pyproject.toml"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for banned in BANNED:
            assert not re.search(rf"[\"'\n ]{re.escape(banned)}[=<>\"'\n\[ ]", text), f"{name} lists {banned}"


def test_no_secrets_or_scans_are_tracked():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore and "*.pdf" in ignore
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert all(line.split("=", 1)[1] == "" or not line.split("=", 1)[1].strip().startswith(("gsk_", "sk-"))
               for line in example.splitlines() if "=" in line and not line.startswith("#"))
