
from __future__ import annotations

import re
from pathlib import Path

from ..cache import (
    ComputeResult,
    DiskCache,
    cost_translate,
    estimate_output_tokens,
    hash_text,
    make_key,
)
from ..client import BodhanClient
from ..languages import detect_script_language, normalize_lang_code
from ..models import ErrorCode, ToolError, Usage, failure, success
from ..workspace import Workspace

MAX_TRANSLATE_CHARS = 15_000
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।॥])(?=\s|$)")
_SRT_BLOCK_RE = re.compile(
    r"(?:^|\n\n)(\d+)\n(\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3})\n(.*?)(?=\n\n\d+\n\d{2}:\d{2}:\d{2}|\Z)",
    re.DOTALL,
)
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".srt", ".vtt"}


def _default_cache(workspace: Workspace, client: BodhanClient | None) -> DiskCache:
    if client is not None and type(client) is not BodhanClient:
        return DiskCache(workspace.out_dir / ".cache")
    return DiskCache.from_env()


def _split_oversized(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = max(
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        split_at = split_at + 1 if split_at > 0 else limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        parts.append(remaining)
    return parts


def _translation_chunks(text: str, limit: int = MAX_TRANSLATE_CHARS) -> list[str]:
    """Chunk text at paragraph/table-line boundaries first, then sentence boundaries."""
    chunks: list[str] = []
    current = ""
    blocks = text.split("\n\n")
    for idx, block in enumerate(blocks):
        piece = block if idx == len(blocks) - 1 else block + "\n\n"
        if len(piece) <= limit:
            if current and len(current) + len(piece) > limit:
                chunks.append(current)
                current = ""
            current += piece
            continue

        # Block exceeds limit: split on lines (keeps Markdown table rows intact)
        if current:
            chunks.append(current)
            current = ""
        lines = piece.splitlines(keepends=True)
        for line in lines:
            if len(line) <= limit:
                if current and len(current) + len(line) > limit:
                    chunks.append(current)
                    current = ""
                current += line
                continue
            for sentence in _SENTENCE_BOUNDARY.split(line):
                for subpiece in _split_oversized(sentence, limit):
                    if current and len(current) + len(subpiece) > limit:
                        chunks.append(current)
                        current = ""
                    current += subpiece
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _parse_srt_blocks(text: str) -> list[tuple[str, str, str]] | None:
    stripped = text.replace("\r\n", "\n").strip()
    if "-->" not in stripped:
        return None
    matches = _SRT_BLOCK_RE.findall(stripped)
    if not matches:
        return None
    return [(num.strip(), ts.strip(), body.strip()) for num, ts, body in matches]


def _translate_piece(
    api_client: BodhanClient,
    disk_cache: DiskCache,
    chunk: str,
    norm_target: str,
    norm_source: str | None,
) -> tuple[str, float, float, int, bool]:
    cache_key = make_key(
        "translate",
        "chunk",
        {
            "model": "indic-translate",
            "target_language": norm_target,
            "source_language": norm_source,
            "target_script": "native",
        },
        [hash_text(chunk)],
    )

    def _compute() -> ComputeResult:
        resp = api_client.translate(
            text=chunk,
            target_language=norm_target,
            source_language=norm_source,
        )
        translated = resp.get("translated_text", "")
        if not isinstance(translated, str) or not translated.strip():
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                "Translation returned empty output for a text chunk.",
                hint="Retry the translation or submit a smaller source document.",
            )
        resp_usage = resp.get("usage")
        cost = (
            resp_usage.cost_inr
            if isinstance(resp_usage, Usage) and resp_usage.cost_inr > 0
            else 0.0
        )
        if not cost and isinstance(resp_usage, Usage):
            tokens = resp_usage.units.get("output_tokens") or estimate_output_tokens(
                len(translated)
            )
            cost = cost_translate(tokens)
        if not cost:
            cost = cost_translate(estimate_output_tokens(len(translated)))
        latency = resp_usage.latency_ms if isinstance(resp_usage, Usage) else 0
        return ComputeResult(
            value={
                "translated_text": translated.strip(),
                "truncated": bool(resp.get("truncated", False)),
                "latency_ms": latency,
            },
            cost_inr=cost,
        )

    cached_res = disk_cache.get_or_compute_sync("translate", cache_key, _compute)
    if cached_res.entry.value.get("truncated") and len(chunk) > 200:
        midpoint = len(chunk) // 2
        split_at = chunk.rfind("\n", 0, midpoint + 1)
        if split_at <= 0:
            split_at = chunk.rfind(" ", 0, midpoint + 1)
        if split_at <= 0:
            split_at = midpoint
        left_res = _translate_piece(
            api_client, disk_cache, chunk[:split_at], norm_target, norm_source
        )
        right_res = _translate_piece(
            api_client, disk_cache, chunk[split_at:], norm_target, norm_source
        )
        return (
            f"{left_res[0]}\n{right_res[0]}",
            left_res[1] + right_res[1],
            left_res[2] + right_res[2],
            left_res[3] + right_res[3],
            left_res[4] and right_res[4],
        )

    return (
        str(cached_res.entry.value["translated_text"]),
        cached_res.charged_inr,
        cached_res.avoided_inr,
        0 if cached_res.cached else int(cached_res.entry.value.get("latency_ms", 0)),
        cached_res.cached,
    )


def translate_text(
    workspace: Workspace,
    text_or_file: str,
    target_language: str,
    source_language: str | None = None,
    client: BodhanClient | None = None,
    *,
    cache: DiskCache | None = None,
) -> dict:
    try:
        norm_target = normalize_lang_code(target_language)
        if norm_target in {"bhb", "bho"}:
            raise ToolError(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                f"Indic-Translate does not support target language {norm_target!r}.",
            )
        norm_source = normalize_lang_code(source_language) if source_language else None
        value = text_or_file.strip()
        source_path: Path | None = None
        if Workspace.is_probable_file_ref(value, _TEXT_SUFFIXES):
            source_path = workspace.resolve_input(value, suffixes=_TEXT_SUFFIXES)
            try:
                content = source_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not decode {source_path.name!r} as UTF-8 text.",
                    hint="Save the file as UTF-8 and try again.",
                ) from exc
            source_name = source_path.stem
        else:
            content = value
            source_name = "translated_text"

        if not content.strip():
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "Translation input content is empty.")

        api_client = client or BodhanClient()
        disk_cache = cache or _default_cache(workspace, client)
        charged_cost = 0.0
        avoided_cost = 0.0
        total_latency_ms = 0
        all_cached = True
        warnings: list[str] = []

        srt_blocks = _parse_srt_blocks(content)
        is_srt = (source_path is not None and source_path.suffix.lower() == ".srt") or (
            srt_blocks is not None
        )

        if is_srt and srt_blocks:
            translated_cues: list[str] = []
            for cue_id, timestamp, body in srt_blocks:
                if not body:
                    translated_cues.append(f"{cue_id}\n{timestamp}\n")
                    continue
                translated_body, c_cost, a_cost, lat, was_cached = _translate_piece(
                    api_client, disk_cache, body, norm_target, norm_source
                )
                charged_cost += c_cost
                avoided_cost += a_cost
                total_latency_ms += lat
                if not was_cached:
                    all_cached = False
                translated_cues.append(f"{cue_id}\n{timestamp}\n{translated_body.strip()}")
            translated_text = "\n\n".join(translated_cues) + "\n"
            chunk_count = len(srt_blocks)
            translated_chunk_count = len(translated_cues)
            out_suffix = ".srt"
            out_kind = "subtitles"
        else:
            chunks = _translation_chunks(content)
            translated_chunks: list[str] = []
            for chunk in chunks:
                translated_piece, c_cost, a_cost, lat, was_cached = _translate_piece(
                    api_client, disk_cache, chunk, norm_target, norm_source
                )
                charged_cost += c_cost
                avoided_cost += a_cost
                total_latency_ms += lat
                if not was_cached:
                    all_cached = False
                translated_chunks.append(translated_piece)
            translated_text = "\n".join(translated_chunks)
            chunk_count = len(chunks)
            translated_chunk_count = len(translated_chunks)
            is_markdown = (
                source_path is not None and source_path.suffix.lower() in {".md", ".markdown"}
            ) or ("| ---" in content or "|---" in content)
            out_suffix = ".md" if is_markdown else ".txt"
            out_kind = "markdown" if is_markdown else "translation"

        source_table_rows = sum(1 for line in content.splitlines() if line.strip().startswith("|"))
        target_table_rows = sum(
            1 for line in translated_text.splitlines() if line.strip().startswith("|")
        )
        if source_table_rows > 0 and target_table_rows < source_table_rows:
            warnings.append(
                f"Table row count changed ({source_table_rows} source rows -> "
                f"{target_table_rows} translated rows); verify table formatting."
            )

        detected_target = detect_script_language(translated_text)
        if detected_target["candidates"] and norm_target not in detected_target["candidates"]:
            warnings.append(
                f"Target script check expected {norm_target!r} but detected script "
                f"{detected_target['script']} ({detected_target['candidates']})."
            )

        artifact = workspace.write_text(
            f"{source_name}-to-{norm_target}",
            out_suffix,
            translated_text,
            kind=out_kind,
        )
        usage = Usage(
            model="indic-translate",
            cost_inr=round(charged_cost, 6),
            latency_ms=total_latency_ms,
            cached=all_cached,
            avoided_inr=round(avoided_cost, 6),
            units={
                "input_characters": float(len(content)),
                "output_characters": float(len(translated_text)),
                "chunks": float(chunk_count),
            },
        )

        return success(
            {
                "target_language": norm_target,
                "source_language": norm_source,
                "character_count": len(translated_text),
                "source_character_count": len(content),
                "translation": translated_text,
                "chunk_count": chunk_count,
                "translated_chunk_count": translated_chunk_count,
                "coverage_complete": translated_chunk_count == chunk_count,
                "table_rows_preserved": (
                    target_table_rows >= source_table_rows if source_table_rows > 0 else None
                ),
            },
            artifacts=[artifact],
            usage=usage,
            warnings=warnings,
        )
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Translation could not read {text_or_file!r}: {exc.strerror or exc}",
            )
        )
    except Exception as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Translation failed: {type(exc).__name__}: {exc}",
                details={"target_language": target_language},
            )
        )
