"""
Executes a single plan step by calling the right tool function and
resolving {step_id.field} / {files[N]} references in the args.

Returns the tool result dict and actual cost in INR.
"""
from __future__ import annotations

import re
from pathlib import Path

from bodhan_mcp.tools.audio_chunk import chunk_audio
from bodhan_mcp.tools.constants import DEFAULT_VOICES
from bodhan_mcp.tools.language_detect import detect_language_from_text
from bodhan_mcp.tools.media_info import get_media_info
from bodhan_mcp.tools.ocr import ocr_file
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

_REF_PATTERN = re.compile(r"\{([^}]+)\}")


def _resolve_refs(value: object, files: list[Path], results: dict[str, dict]) -> object:
    """Recursively resolve {files[N]} and {step_id.field} references."""
    if isinstance(value, str):
        # Exact reference like "{s2.segments}" -> preserve raw object type (e.g. list, dict)
        if value.startswith("{") and value.endswith("}") and value.count("{") == 1:
            ref = value[1:-1]
            if ref.startswith("files["):
                bracket_end = ref.index("]")
                idx = int(ref[6:bracket_end])
                fpath = files[idx]
                attr = ref[bracket_end + 1:].lstrip(".")
                if attr in ("content", "text"):
                    try:
                        return fpath.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        return str(fpath)
                return str(fpath)
            parts = ref.split(".", 1)
            step_id = parts[0]
            field = parts[1] if len(parts) > 1 else None
            step_result = results.get(step_id, {})
            if field:
                val = step_result.get(field)
                if val is None:
                    if field in ("transcribed_text", "transcript"):
                        val = step_result.get("text")
                    elif field in ("summary", "summary_text"):
                        val = step_result.get("summary_text") or step_result.get("text")
                    elif field in ("translated_text", "translation"):
                        val = step_result.get("translated_text")
                return val if val is not None else value
            return step_result

        def _replace(m: re.Match) -> str:
            ref = m.group(1)
            if ref.startswith("files["):
                bracket_end = ref.index("]")
                idx = int(ref[6:bracket_end])
                fpath = files[idx]
                attr = ref[bracket_end + 1:].lstrip(".")
                if attr in ("content", "text"):
                    try:
                        return fpath.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        return str(fpath)
                return str(fpath)
            parts = ref.split(".", 1)
            step_id = parts[0]
            field = parts[1] if len(parts) > 1 else None
            step_result = results.get(step_id, {})
            if field:
                val = step_result.get(field)
                if val is None:
                    if field in ("transcribed_text", "transcript"):
                        val = step_result.get("text")
                    elif field in ("summary", "summary_text"):
                        val = step_result.get("summary_text") or step_result.get("text")
                    elif field in ("translated_text", "translation"):
                        val = step_result.get("translated_text")
                return str(val) if val is not None else m.group(0)
            return str(step_result)
        return _REF_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_refs(v, files, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, files, results) for v in value]
    return value


async def execute_step(
    step: dict,
    files: list[Path],
    results: dict[str, dict],
    out_dir: Path,
) -> tuple[dict, float]:
    """Execute one plan step. Returns (result_dict, actual_cost_inr)."""
    tool_name = step["tool"]
    args: dict = _resolve_refs(step.get("args", {}), files, results)  # type: ignore[assignment]

    if tool_name == "transcribe":
        result = await transcribe_audio(Path(args["audio_path"]), language=args.get("language"))
        return result, result.pop("cost_inr", 0.0)

    if tool_name == "translate":
        text = args["text"]
        try:
            p = Path(text)
            if p.is_file() and p.suffix.lower() in (".txt", ".md"):
                text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        result = await translate_text(text, args["source_lang"], args["target_lang"])
        # Save translated text into artifacts
        try:
            target_lang = args.get("target_lang", "trans")
            out_file = out_dir / f"translated_{target_lang}.txt"
            out_file.write_text(result["translated_text"], encoding="utf-8")
        except Exception:
            pass
        return result, result.pop("cost_inr", 0.0)

    if tool_name == "speak":
        text = args["text"]
        try:
            p = Path(text)
            if p.is_file() and p.suffix.lower() in (".txt", ".md"):
                text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        language = args["language"]
        voice = args.get("voice")
        if not voice or voice.lower() in ("female", "f"):
            voice = DEFAULT_VOICES.get(language, {}).get("female", "Kavya")
        elif voice.lower() in ("male", "m"):
            voice = DEFAULT_VOICES.get(language, {}).get("male", "Amit")
        speak_dir = out_dir
        result = await synthesize_speech(text, language, voice, speak_dir)
        return result, result.pop("cost_inr", 0.0)

    if tool_name == "ocr":
        result = await ocr_file(Path(args["file_path"]))
        return result, result.pop("cost_inr", 0.0)

    if tool_name == "language_detect":
        return detect_language_from_text(args["text"]), 0.0

    if tool_name == "audio_chunk":
        max_sec = float(args.get("max_seconds", 28.0))
        paths = chunk_audio(Path(args["audio_path"]), max_seconds=max_sec)
        return {"chunks": [str(p) for p in paths], "count": len(paths)}, 0.0

    if tool_name == "media_info":
        return get_media_info(Path(args["file_path"])), 0.0

    if tool_name == "summarize":
        from bodhan_mcp.tools.summarize import summarize_english
        target_duration = int(args.get("target_duration_seconds", 120))
        result = await summarize_english(args["text"], target_duration_seconds=target_duration)
        try:
            (out_dir / "summary_en.txt").write_text(result["summary_text"], encoding="utf-8")
        except Exception:
            pass
        return result, 0.0

    if tool_name in ("subtitles", "create_subtitles"):
        from bodhan_mcp.tools.subtitles import create_srt_subtitles
        raw_name = Path(args.get("out_path", "subtitles_en.srt")).name
        srt_path = out_dir / raw_name
        text_arg = args.get("text", "")
        if isinstance(text_arg, dict):
            text_arg = text_arg.get("translated_text") or text_arg.get("text") or ""
        result = create_srt_subtitles(str(text_arg), args.get("segments"), srt_path)
        return result, 0.0

    raise ValueError(f"Unknown tool: {tool_name!r}")
