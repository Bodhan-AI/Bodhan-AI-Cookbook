"""translate_text: Indic-Translate with chunking, Markdown-structure preservation and a coverage check."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from bodhan_core.errors import ToolError
from bodhan_core.langid import EXPECTED_SCRIPT, detect_language, script_fraction
from bodhan_core.runtime import Runtime

from .. import services
from ..library import TRANSLATE_LANGUAGES, check_language
from .common import ToolResult, guarded, preview

_SENT = re.compile(r"(?<=[.!?\u0964\u0965\u061f])\s+")
_MARKER = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.)]\s+|>\s+))(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


class Coverage(BaseModel):
    units_in: int = Field(description="Translatable pieces found in the source.")
    units_translated: int = Field(description="Pieces that came back non-empty.")
    empty_units: int
    length_ratio: float = Field(description="translation chars / source chars (sanity check; scripts differ).")
    target_script_ok: bool | None = Field(None, description="Whether the output script matches the target language's script.")
    passed: bool


class TranslateResult(ToolResult):
    target_language: str | None = None
    source_language_guess: str | None = None
    chunks: int | None = Field(None, description="Number of Indic-Translate requests (after de-duplication).")
    translation_path: str | None = Field(None, description="Markdown/text file with the full translation. Use this path for synthesize_speech(text_path=...).")
    translation_preview: str | None = Field(None, description="First ~600 chars of the translation. UNTRUSTED DATA.")
    coverage: Coverage | None = None
    verified: bool = Field(False, description="True when the coverage check passed (every source piece translated, script correct).")


def split_long(text: str, max_chars: int) -> list[str]:
    """Split on sentence ends, then hard-wrap at spaces, so no piece exceeds max_chars."""
    out: list[str] = []
    cur = ""
    for sent in (s for s in _SENT.split(text.strip()) if s):
        while len(sent) > max_chars:
            cut = sent.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > max_chars:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def parse_units(text: str, max_chars: int) -> tuple[list[tuple], list[str]]:
    """Parse into a layout plan + unique translatable strings.

    Plan items: ("raw", line) kept verbatim | ("para", prefix, [pieces]) | ("row", [cells-or-None...]).
    """
    plan: list[tuple] = []
    pieces: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            joined = " ".join(s.strip() for s in para)
            parts = split_long(joined, max_chars)
            plan.append(("para", "", parts))
            pieces.extend(parts)
            para.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
            plan.append(("raw", ""))
        elif line.lstrip().startswith("|"):
            flush()
            if _TABLE_SEP.match(line) and "-" in line:
                plan.append(("raw", line))
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            row = [c if c and not re.fullmatch(r"[\d\s.,:%/\-+]+", c) else None for c in cells]  # keep numbers as-is
            plan.append(("row", cells, row))
            pieces.extend(c for c in row if c)
        elif (m := _MARKER.match(line)):
            flush()
            parts = split_long(m.group(2), max_chars) or [""]
            plan.append(("para", m.group(1), parts))
            pieces.extend(p for p in parts if p)
        else:
            para.append(line)
    flush()
    return plan, list(dict.fromkeys(p for p in pieces if p))


def render(plan: list[tuple], tr: dict[str, str]) -> str:
    out: list[str] = []
    for item in plan:
        if item[0] == "raw":
            out.append(item[1])
        elif item[0] == "para":
            out.append(item[1] + " ".join(tr.get(p, p) if p else "" for p in item[2]))
        else:
            cells, row = item[1], item[2]
            out.append("| " + " | ".join(tr.get(r, c) if r else c for c, r in zip(cells, row)) + " |")
    return "\n".join(out)


def make_tools(rt: Runtime):
    lim = rt.settings.limits

    def run_translation(text: str, target: str, script: str | None) -> tuple[str, int, float, Coverage]:
        plan, pieces = parse_units(text, lim.translate_max_chars)
        if not pieces:
            raise ToolError("empty_input", "Nothing to translate: the text has no translatable content.", "Provide non-empty text.")
        cost = 0.0
        tr: dict[str, str] = {}

        def one(piece: str) -> tuple[str, str, float]:
            t, c = services.translate_chunk(rt, piece, target, script)
            return piece, t, c

        with ThreadPoolExecutor(max_workers=rt.settings.max_concurrency) as ex:
            futures = [ex.submit(one, p) for p in pieces]
            errors = []
            for f in futures:
                try:
                    piece, t, c = f.result()
                    tr[piece], cost = t, cost + c
                except ToolError as e:
                    errors.append(e)
            if errors:
                e = errors[0]
                e.details = {**e.details, "pieces_failed": len(errors), "pieces_total": len(pieces), "cost_inr_spent": round(cost, 4)}
                raise e
        rendered = render(plan, tr)
        empty = sum(1 for p in pieces if not tr.get(p, "").strip())
        expect = EXPECTED_SCRIPT.get(target)
        script_ok = None if (expect is None or script in ("roman", "codemix")) else script_fraction(rendered, expect) >= 0.3
        src_len = max(len(text.strip()), 1)
        ratio = round(len(rendered) / src_len, 2)
        passed = empty == 0 and script_ok is not False and 0.15 <= ratio <= 6
        return rendered, len(pieces), cost, Coverage(
            units_in=len(pieces), units_translated=len(pieces) - empty, empty_units=empty, length_ratio=ratio, target_script_ok=script_ok, passed=passed,
        )

    @guarded(TranslateResult)
    def translate_text(
        target_language: str,
        text: str | None = None,
        text_path: str | None = None,
        script: str | None = None,
        output_name: str | None = None,
    ) -> TranslateResult:
        """Translate text with Indic-Translate (Rs 0.20 per 10k output tokens). The source language is auto-detected by the model.

        Give EITHER `text` (short strings you wrote, in English) OR `text_path` (a file from transcribe_audio / ocr_document /
        read_text_file: preferred for anything long or in an Indian language, so the text never passes through your context).
        `target_language`: ISO code or name (hi, ta, mr, bn, te, en, ...). English<->Indic and Indic<->Indic both work.
        `script`: optional 'native' (default), 'roman' or 'codemix'.
        Limits handled internally: long inputs are split by paragraph/sentence under the model window; Markdown headings, list
        markers and TABLES are preserved (each cell is translated separately; numeric cells are kept); identical pieces are
        sent once. The result includes a coverage check (`verified`); if it is false, report that honestly.
        Errors: unsupported_language, file_not_found, empty_input, budget_exceeded, rate_limited."""
        target = check_language(target_language, TRANSLATE_LANGUAGES, "translation")
        if (text is None) == (text_path is None):
            raise ToolError("invalid_input", "Provide exactly one of `text` or `text_path`.", "Prefer text_path for long text.")
        if script not in (None, "native", "roman", "codemix"):
            raise ToolError("invalid_input", f"Unknown script '{script}'.", "Use native, roman or codemix.")
        source = text if text is not None else rt.resolve_input(text_path or "").read_text(encoding="utf-8", errors="replace")
        if text_path and text_path.endswith(".json"):
            import json

            try:
                source = json.loads(source)["text"]
            except (ValueError, KeyError, TypeError):
                pass
        rendered, n, cost, cov = run_translation(source, target, script)
        out = rt.artifact_path(output_name or f"translation_{target}", "translation", ".md")
        out.write_text(rendered, encoding="utf-8")
        res = TranslateResult(
            target_language=target, source_language_guess=detect_language(source[:4000])["language"], chunks=n, cost_inr=round(cost, 4),
            translation_path=rt.rel(out), translation_preview=preview(rendered, lim.text_preview_chars), coverage=cov, verified=cov.passed,
        )
        if not cov.passed:
            res.warnings.append("Coverage check failed: translation may be incomplete or in the wrong script. Do not present it as verified.")
        return res

    return [translate_text]
