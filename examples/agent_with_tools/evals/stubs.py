"""Stub versions of the PAID Bodhan tools for stub-mode evals (Rs 0).

The stubs keep each real tool's name, description and argument schema, so the LLM sees exactly what it sees in
production; only the model call behind the tool is replaced by a canned, plausible result. Free tools
(inspect_media, detect_language, read_text_file, write_text_file) stay real. The agent's own harness code
(verification, budgets, injection screening) runs unchanged on top of these results.

What a stub run can and cannot show: it tests the planner's decisions (which tool, which arguments, when to stop
or refuse) and the harness around it. It says nothing about Bodhan's output quality: run live cases for that.
"""

from __future__ import annotations

import json
import re
import wave
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from bodhan_core.langid import detect_language
from evals import cassettes

PAID_TOOLS = ("transcribe_audio", "translate_text", "speak_text", "ocr_document", "make_subtitles")

# One short sentence per language (script-correct, so the agent's script/coverage checks behave as in production).
SAMPLE = {
    "hi": "यह एक नमूना अनुवाद है।",
    "mr": "हा एक नमुना अनुवाद आहे.",
    "ta": "இது ஒரு மாதிரி மொழிபெயர்ப்பு.",
    "te": "ఇది ఒక నమూనా అనువాదం.",
    "bn": "এটি একটি নমুনা অনুবাদ।",
    "gu": "આ એક નમૂના અનુવાદ છે.",
    "kn": "ಇದು ಒಂದು ಮಾದರಿ ಅನುವಾದ.",
    "ml": "ഇത് ഒരു സാമ്പിൾ വിവർത്തനമാണ്.",
    "pa": "ਇਹ ਇੱਕ ਨਮੂਨਾ ਅਨੁਵਾਦ ਹੈ।",
    "en": "This is a sample translation of the text.",
}
_INR_TRANSCRIBE_PER_MIN, _INR_OCR_PER_PAGE, _INR_SPEAK_PER_CHAR = 0.10, 0.20, 6 / 10_000


def _fill(sentence: str, length: int) -> str:
    """Whole copies of the sentence totalling roughly `length` characters (never cut mid-sentence, so it reads as finished text)."""
    return " ".join([sentence] * max(1, round(length / (len(sentence) + 1))))


def translate_like(source: str, target: str) -> str:
    """Line-for-line stand-in translation: same line and table-row structure, similar length, target script."""
    sentence = SAMPLE.get(target, SAMPLE["hi"])
    out = []
    for line in source.splitlines():
        if not line.strip():
            out.append("")
        elif line.strip().startswith("|"):
            cells = [c for c in line.strip().strip("|").split("|")]
            out.append("| " + " | ".join("---" if set(c.strip()) <= {"-", ":"} and c.strip() else sentence for c in cells) + " |")
        else:
            out.append(_fill(sentence, len(line)))
    return "\n".join(out)


def _read(path: str | None) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8-sig") if path else None
    except OSError:
        return None


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _silent_wav(path: Path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(b"\x00\x00" * int(24_000 * seconds))


def _lang_fields(text: str) -> dict:
    found = detect_language(text) if text else None
    if not found:
        return {"detected_language": None, "script": None, "language_candidates": []}
    return {"detected_language": found.language, "script": found.script, "language_candidates": found.candidates}


def _is_indic(text: str) -> bool:
    return bool(re.search(r"[ऀ-෿]", text))


class StubBackend:
    """Canned results for the paid tools. One instance per case run; keeps the files and calls it saw."""

    def __init__(self, spec: dict, work_dir: Path, cassette: dict | None = None):
        self.spec, self.work = spec, work_dir
        self.cassette = cassette  # real recorded results to replay (evals/cassettes/<case>.json), or None
        self.hits = 0
        self.misses: list[str] = []  # lookups the cassette could not answer; those fell back to the hand-written stub
        self.root = (work_dir / "out" / "artifacts").resolve()  # where the real tools may write (BODHAN_OUTPUT_ROOT)
        self.spoken: dict[str, str] = {}  # generated audio path -> the text that was "spoken" (round-trips through transcribe)
        self.n = 0

    def _scratch(self, stem: str, suffix: str) -> Path:
        self.n += 1
        return self.work / "stub_work" / f"{stem}_{self.n}{suffix}"

    def _out(self, raw: str | None) -> Path | None:
        """Same rule as the real write tools: relative paths live under the output root, nothing may land outside it."""
        if not raw:
            return None
        p = Path(raw)
        p = (p if p.is_absolute() else self.root / p).resolve()
        return p if p.is_relative_to(self.root) else None

    @staticmethod
    def _outside(raw: str) -> str:
        return json.dumps({"ok": False, "error": {"code": "path_outside_output_root", "message": f"Refusing to write `{raw}`: files may only be written under the output folder.", "retryable": False}})

    def _lookup(self, finder, tool: str, label: str, *key) -> dict | None:
        """Cassette lookup that counts hits and misses. No cassette for the case: nothing to count."""
        if self.cassette is None:
            return None
        found = finder(self.cassette, *key) if finder is cassettes.find_translation else finder(self.cassette, tool, *key)
        if found:
            self.hits += 1
        else:
            self.misses.append(f"{tool}({label})")
        return found

    def _text_arg(self, args: dict) -> str:
        return str(args["text"]) if args.get("text") else (_read(args.get("text_path")) or "")

    async def translate_text(self, **args) -> str:
        source, target = self._text_arg(args), str(args.get("target_language", ""))
        if not source.strip():
            return json.dumps({"ok": False, "error": {"code": "empty_input", "message": "Give `text` or a readable `text_path`.", "retryable": False}})
        if args.get("output_path") and not self._out(args["output_path"]):
            return self._outside(args["output_path"])
        recorded = self._lookup(cassettes.find_translation, "translate_text", f"{target}: {source[:60]!r}", source, target)
        translation = (recorded or {}).get("translation") or self.spec.get("translate_text", {}).get("by_target", {}).get(target) or translate_like(source, target)
        tokens = max(len(translation) // 3, 1)
        res = {"ok": True, "target_language": target, "truncated": False, "chunks": 1, "output_tokens": tokens,
               "cost_inr": round(tokens * 0.20 / 10_000, 6), **{"source_language": _lang_fields(source)["detected_language"]}}
        path = args.get("output_path")
        if path or _is_indic(translation):
            res["translation_path"] = _write(self._out(path) if path else self._scratch("translation", ".txt"), translation)
        if _is_indic(translation):
            res.update(translation="", translation_is_preview=True)
        else:
            res["translation"] = translation
        return json.dumps(res, ensure_ascii=False)

    async def transcribe_audio(self, **args) -> str:
        path = str(args.get("audio_path", ""))
        p = Path(path)
        if args.get("output_path") and not self._out(args["output_path"]):
            return self._outside(args["output_path"])
        if not p.exists():
            return json.dumps({"ok": False, "error": {"code": "file_not_found", "message": f"`{path}` does not exist.", "retryable": False}})
        t = self.spec.get("transcribe_audio", {})
        recorded = None
        if path in self.spoken:  # back-transcription of audio the agent generated: a perfect round trip
            text, duration = self.spoken[path], max(len(self.spoken[path]) / 15, 1.0)
        else:
            recorded = self._lookup(cassettes.find_file, "transcribe_audio", p.name, p.name)
            lang = t.get("by_file", {}).get(p.name, t.get("default_language", "hi"))
            try:
                with wave.open(path) as w:
                    duration = w.getnframes() / w.getframerate()
            except (wave.Error, EOFError):
                duration = 10.0
            text = recorded["text"] if recorded else SAMPLE.get(lang, SAMPLE["hi"])
            duration = recorded.get("duration_s", duration) if recorded else duration
        half = len(text) // 2
        segments = (recorded or {}).get("segments") if path not in self.spoken else None
        segments = segments or [{"start_s": 0.0, "end_s": round(duration / 2, 2), "text": text[:half]}, {"start_s": round(duration / 2, 2), "end_s": round(duration, 2), "text": text[half:]}]
        res = {"ok": True, "duration_s": round(duration, 2), "cost_inr": round(duration / 60 * _INR_TRANSCRIBE_PER_MIN, 6),
               "segments_path": _write(self._scratch("segments", ".json"), json.dumps(segments, ensure_ascii=False)),
               **_lang_fields(text)}
        if _is_indic(text) or args.get("output_path"):
            res.update(text="", text_truncated=_is_indic(text), text_path=_write(self._out(args["output_path"]) if args.get("output_path") else self._scratch("transcript", ".txt"), text))
        else:
            res.update(text=text, text_truncated=False)
        return json.dumps(res, ensure_ascii=False)

    async def speak_text(self, **args) -> str:
        text, out = self._text_arg(args), str(args.get("output_path", ""))
        if not text.strip() or not out:
            return json.dumps({"ok": False, "error": {"code": "bad_input", "message": "Give `text` or `text_path`, and an `output_path` ending in .wav.", "retryable": False}})
        target = self._out(out)
        if target is None:
            return self._outside(out)
        _silent_wav(target)
        out = str(target)
        self.spoken[out] = text
        found = _lang_fields(text)["detected_language"]
        return json.dumps({"ok": True, "audio_path": out, "voice": "stub-voice", "duration_s": round(max(len(text) / 15, 1.0), 2), "chunks": 1,
                           "characters": len(text), "cost_inr": round(len(text) * _INR_SPEAK_PER_CHAR, 6),
                           "language": args.get("language") or found, "notice": "AI-generated audio (stub)."}, ensure_ascii=False)

    async def ocr_document(self, **args) -> str:
        path = Path(str(args.get("file_path", "")))
        if not path.exists():
            return json.dumps({"ok": False, "error": {"code": "file_not_found", "message": f"`{path}` does not exist.", "retryable": False}})
        if args.get("output_path") and not self._out(args["output_path"]):
            return self._outside(args["output_path"])
        o = self.spec.get("ocr_document", {})
        lines = o.get("lines") or [SAMPLE.get(o.get("lang", "hi"), SAMPLE["hi"])] * o.get("line_count", 3)
        recorded = self._lookup(cassettes.find_file, "ocr_document", path.name, path.name)
        markdown = recorded["markdown"] if recorded else "\n".join(lines)
        pages = 1
        if path.suffix.lower() == ".pdf":
            from bodhan_core import media

            total = media.details(path, "pdf").get("pages", 1)
            first, last = int(args.get("first_page") or 1), min(int(args.get("last_page") or total), total)
            pages = max(last - first + 1, 1)
        res = {"ok": True, "page_count": pages, "pages": [{"page": i + 1, "chars": len(markdown) // pages} for i in range(pages)],
               "cost_inr": round(pages * _INR_OCR_PER_PAGE, 6), **_lang_fields(markdown)}
        if args.get("output_path") or _is_indic(markdown) or len(markdown) > 3000:
            res["markdown_path"] = _write(self._out(args["output_path"]) if args.get("output_path") else self._scratch("ocr", ".md"), markdown)
        if _is_indic(markdown):
            res.update(markdown="", markdown_is_preview=True)
        else:
            res["markdown"] = markdown
        return json.dumps(res, ensure_ascii=False)

    async def make_subtitles(self, **args) -> str:
        seg_path, out, target = str(args.get("segments_path", "")), str(args.get("output_path", "")), args.get("target_language")
        try:
            segments = json.loads(Path(seg_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return json.dumps({"ok": False, "error": {"code": "bad_segments_file", "message": "Pass the `segments_path` returned by transcribe_audio.", "retryable": False}})
        if not out.endswith(".srt"):
            return json.dumps({"ok": False, "error": {"code": "bad_output_path", "message": "`output_path` must end in .srt.", "retryable": False}})

        def ts(s: float) -> str:
            ms = int(round(s * 1000))
            return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

        cues = [f"{i}\n{ts(s['start_s'])} --> {ts(s['end_s'])}\n{SAMPLE[target] if target in SAMPLE else s['text']}\n" for i, s in enumerate(segments, 1)]
        _write(Path(out), "\n".join(cues))
        cost = round(len(cues) * 0.0002, 6) if target else 0.0
        return json.dumps({"ok": True, "output_path": out, "cues": len(cues), "segments_in": len(segments), "target_language": target, "cost_inr": cost}, ensure_ascii=False)


def with_stubs(real_tools: list[BaseTool], backend: StubBackend) -> list[BaseTool]:
    """Replace the paid tools by stubs that keep the real name, description and argument schema."""
    out: list[BaseTool] = []
    for tool in real_tools:
        fn = getattr(backend, tool.name, None) if tool.name in PAID_TOOLS else None
        out.append(tool if fn is None else StructuredTool(name=tool.name, description=tool.description, args_schema=tool.args_schema, coroutine=fn))
    return out
