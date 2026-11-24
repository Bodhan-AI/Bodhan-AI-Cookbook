"""Generate a sync HTML reader that highlights sections while narration plays.

Uses ``start_s`` / ``duration_s`` from the narration script (set at Speak).
When a section has units, their windows are split by character length inside
the section (approximate; no extra Speak cost).
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _char_weight(text: str) -> int:
    return max(len((text or "").strip()), 1)


def _fmt_seconds(value: Any) -> str | None:
    seconds = _safe_float(value, default=-1.0)
    if seconds < 0:
        return None
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}m {rem:.0f}s"


def _fmt_cer(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return None


def load_report(path: Path | None) -> dict[str, Any] | None:
    """Load ``report.json`` when present."""
    if path is None:
        return None
    report_path = Path(path)
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_reader_metrics(
    lang: str,
    script: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect UI metrics from the narration script and optional report.json."""
    qa_summary = script.get("qa_summary") or {}
    sections = script.get("sections") or []
    audio_s = 0.0
    for section in sections:
        start = section.get("start_s")
        dur = _safe_float(section.get("duration_s"))
        if start is None:
            continue
        audio_s = max(audio_s, _safe_float(start) + dur)

    metrics: dict[str, Any] = {
        "lang": lang,
        "voice": script.get("voice"),
        "qa_status": qa_summary.get("status") or script.get("status"),
        "mean_cer": qa_summary.get("mean_cer"),
        "audio_s": round(audio_s, 3) if audio_s else None,
        "sections_ok": qa_summary.get("ok"),
        "sections_failed": qa_summary.get("failed"),
        "speak_s": None,
        "qa_s": None,
        "lang_wall_s": None,
        "bodhan_inr_total": None,
        "llm_usd_total": None,
        "run_mean_cer": None,
        "page_count": None,
        "source": "script",
    }

    if not report:
        return metrics

    metrics["source"] = "report"
    metrics["page_count"] = report.get("page_count")
    qa_block = report.get("qa") or {}
    if qa_block.get("mean_cer") is not None:
        metrics["run_mean_cer"] = qa_block.get("mean_cer")

    lang_row = (report.get("languages") or {}).get(lang) or {}
    lang_qa = lang_row.get("qa") or {}
    if lang_qa.get("mean_cer") is not None:
        metrics["mean_cer"] = lang_qa.get("mean_cer")
    if lang_row.get("status"):
        metrics["qa_status"] = lang_row.get("status")
    if lang_row.get("voice"):
        metrics["voice"] = lang_row.get("voice")

    timings = lang_row.get("timings") or {}
    speak_s = timings.get("speak_s")
    qa_s = timings.get("qa_s")
    narrate_s = timings.get("narrate_s")
    translate_s = timings.get("translate_s")
    metrics["speak_s"] = speak_s
    metrics["qa_s"] = qa_s
    parts = [
        _safe_float(translate_s),
        _safe_float(narrate_s),
        _safe_float(speak_s),
        _safe_float(qa_s),
    ]
    if any(parts):
        metrics["lang_wall_s"] = round(sum(parts), 3)

    cost = report.get("cost") or {}
    bodhan = cost.get("bodhan_inr") or {}
    llm = cost.get("llm_usd") or {}
    if bodhan.get("total") is not None:
        metrics["bodhan_inr_total"] = bodhan.get("total")
    if llm.get("total_usd") is not None:
        metrics["llm_usd_total"] = llm.get("total_usd")

    return metrics


def build_cues(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Build timed cues for the HTML player from a narration script."""
    cues: list[dict[str, Any]] = []
    for section in script.get("sections") or []:
        if section.get("start_s") is None:
            continue
        start = _safe_float(section.get("start_s"))
        duration = max(_safe_float(section.get("duration_s")), 0.0)
        end = start + duration
        sec_id = str(section.get("id") or f"sec_{len(cues) + 1}")
        title = (section.get("title") or "").strip() or sec_id
        units = [u for u in (section.get("units") or []) if (u.get("text_for_tts") or "").strip()]
        cer = (section.get("qa") or {}).get("cer")

        if not units:
            text = (section.get("text_for_tts") or title).strip()
            cues.append(
                {
                    "id": sec_id,
                    "section_id": sec_id,
                    "kind": "section",
                    "title": title,
                    "text": text,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "cer": cer,
                }
            )
            continue

        # Title line first (short share), then units by character weight.
        title_text = title
        weights = [_char_weight(title_text)] + [
            _char_weight(u.get("text_for_tts") or "") for u in units
        ]
        total_w = sum(weights) or 1
        cursor = start
        span = max(end - start, 0.0)

        title_dur = span * (weights[0] / total_w)
        cues.append(
            {
                "id": f"{sec_id}__title",
                "section_id": sec_id,
                "kind": "title",
                "title": title,
                "text": title_text,
                "start": round(cursor, 3),
                "end": round(cursor + title_dur, 3),
                "cer": cer,
            }
        )
        cursor += title_dur

        for unit, weight in zip(units, weights[1:], strict=True):
            unit_dur = span * (weight / total_w)
            unit_id = str(unit.get("id") or "u")
            cues.append(
                {
                    "id": f"{sec_id}__{unit_id}",
                    "section_id": sec_id,
                    "kind": str(unit.get("kind") or "prose"),
                    "title": title,
                    "text": (unit.get("text_for_tts") or "").strip(),
                    "start": round(cursor, 3),
                    "end": round(cursor + unit_dur, 3),
                    "cer": None,
                }
            )
            cursor += unit_dur

        # Absorb float remainder into last cue.
        if cues and cues[-1]["section_id"] == sec_id:
            cues[-1]["end"] = round(end, 3)

    return cues


def _metrics_panel_html(metrics: dict[str, Any] | None) -> str:
    if not metrics:
        return ""
    items: list[tuple[str, str]] = []
    if metrics.get("lang"):
        items.append(("Lang", str(metrics["lang"])))
    cer = _fmt_cer(metrics.get("mean_cer"))
    if cer:
        items.append(("Mean CER", cer))
    audio = _fmt_seconds(metrics.get("audio_s"))
    if audio:
        items.append(("Audio", audio))
    speak = _fmt_seconds(metrics.get("speak_s"))
    if speak:
        items.append(("Speak time", speak))
    qa_t = _fmt_seconds(metrics.get("qa_s"))
    if qa_t:
        items.append(("QA time", qa_t))
    wall = _fmt_seconds(metrics.get("lang_wall_s"))
    if wall:
        items.append(("Lang wall", wall))
    if metrics.get("bodhan_inr_total") is not None:
        items.append(("Bodhan (run)", f"₹{float(metrics['bodhan_inr_total']):.2f}"))
    if metrics.get("llm_usd_total") is not None:
        items.append(("Gemini (run)", f"${float(metrics['llm_usd_total']):.4f}"))
    if metrics.get("voice"):
        items.append(("Voice", str(metrics["voice"])))
    if metrics.get("qa_status"):
        items.append(("Status", str(metrics["qa_status"])))
    if metrics.get("page_count") is not None:
        items.append(("Pages", str(metrics["page_count"])))
    run_cer = _fmt_cer(metrics.get("run_mean_cer"))
    if run_cer and run_cer != cer:
        items.append(("Run mean CER", run_cer))
    if not items:
        return ""
    cells = "\n".join(
        f'<div class="metric"><span class="metric-label">{html.escape(label)}</span>'
        f'<span class="metric-value">{html.escape(value)}</span></div>'
        for label, value in items
    )
    note = (
        "From report.json (run cost is shared across languages)."
        if metrics.get("source") == "report"
        else "From narration script (open after a full run for cost/timings)."
    )
    return (
        f'<aside class="metrics" aria-label="Run metrics">'
        f'<div class="metrics-grid">{cells}</div>'
        f'<p class="metrics-note">{html.escape(note)}</p>'
        f"</aside>"
    )


_TEMPLATE_PATH = Path(__file__).with_name("reader_template.html")


def _load_reader_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _cue_blocks_html(cues: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for cue in cues:
        cid = html.escape(str(cue["id"]))
        kind = html.escape(str(cue.get("kind") or "prose"))
        text = html.escape(str(cue.get("text") or "")).replace("\n", "<br>\n")
        tag = "h2" if cue.get("kind") in ("title", "section") else "p"
        cer_badge = ""
        cer_txt = _fmt_cer(cue.get("cer"))
        if cer_txt and cue.get("kind") in ("title", "section"):
            cer_badge = f'<span class="cer-badge">CER {html.escape(cer_txt)}</span>'
        heading = (
            f'<{tag}><span class="cue-text">{text}</span>{cer_badge}</{tag}>'
            if tag == "h2"
            else f"<{tag}>{text}</{tag}>"
        )
        blocks.append(
            f'<section class="cue" data-id="{cid}" data-kind="{kind}" '
            f'data-start="{cue["start"]}" data-end="{cue["end"]}">'
            f"{heading}</section>"
        )
    return "\n".join(blocks) if blocks else '<p class="empty">No timed sections.</p>'


def render_reader_html(
    *,
    lang: str,
    audio_name: str,
    cues: list[dict[str, Any]],
    title: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> str:
    """Return a self-contained HTML document (cues embedded as JSON)."""
    page_title = html.escape(title or f"Narrated lesson ({lang})")
    template = _load_reader_template()
    return (
        template.replace("__LANG__", html.escape(lang))
        .replace("__PAGE_TITLE__", page_title)
        .replace("__METRICS_HTML__", _metrics_panel_html(metrics))
        .replace("__BODY_BLOCKS__", _cue_blocks_html(cues))
        .replace("__AUDIO_NAME__", html.escape(audio_name))
        .replace("__CUES_JSON__", json.dumps(cues, ensure_ascii=False))
    )


def write_reader_for_script(
    script_path: Path,
    *,
    out_path: Path | None = None,
    audio_name: str | None = None,
    report: dict[str, Any] | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Build ``reader.html`` beside the lesson audio for one narration script."""
    script_path = Path(script_path)
    data = json.loads(script_path.read_text(encoding="utf-8"))
    lang = str(data.get("lang") or script_path.stem.split(".")[-1] or "und")
    lang_dir = script_path.parent
    audio = audio_name or f"lesson.{lang}.mp3"
    lesson_mp3 = lang_dir / audio
    if not lesson_mp3.is_file():
        # Fall back to basename from script field if present.
        raw = data.get("lesson_mp3")
        if raw:
            candidate = Path(str(raw))
            if candidate.is_file():
                audio = candidate.name
            elif (lang_dir / candidate.name).is_file():
                audio = candidate.name

    report_data = report if report is not None else load_report(report_path)
    if report_data is None:
        # Default: sibling ../report.json when writing under out/{lang}/
        report_data = load_report(lang_dir.parent / "report.json")

    cues = build_cues(data)
    metrics = build_reader_metrics(lang, data, report_data)
    title = None
    for section in data.get("sections") or []:
        if (section.get("title") or "").strip():
            title = (section.get("title") or "").strip()
            break
    html_doc = render_reader_html(
        lang=lang,
        audio_name=audio,
        cues=cues,
        title=title,
        metrics=metrics,
    )
    target = Path(out_path) if out_path else lang_dir / "reader.html"
    target.write_text(html_doc, encoding="utf-8")

    data["reader_html"] = str(target)
    data["reader_cue_count"] = len(cues)
    data["reader_metrics"] = metrics
    script_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "lang": lang,
        "status": "ok" if cues else "empty",
        "path": str(target),
        "cue_count": len(cues),
        "audio": audio,
        "metrics": metrics,
    }


def write_readers_for_speaks(
    out_dir: Path,
    speaks: list[dict[str, Any]],
    *,
    report: dict[str, Any] | None = None,
    report_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Write a reader for each successful speak language under ``out_dir``."""
    results: list[dict[str, Any]] = []
    out_dir = Path(out_dir)
    report_data = report if report is not None else load_report(report_path or (out_dir / "report.json"))
    for row in speaks:
        lang = row.get("lang")
        if not lang:
            continue
        if row.get("status") not in ("ok", "partial"):
            results.append(
                {
                    "lang": lang,
                    "status": "skipped",
                    "path": None,
                    "cue_count": 0,
                    "error": row.get("error") or "speak not ok",
                }
            )
            continue
        script_path = row.get("script_path")
        if not script_path:
            script_path = out_dir / str(lang) / f"narration_script.{lang}.json"
        try:
            results.append(
                write_reader_for_script(
                    Path(script_path),
                    report=report_data,
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "lang": lang,
                    "status": "error",
                    "path": None,
                    "cue_count": 0,
                    "error": str(exc),
                }
            )
    return results
