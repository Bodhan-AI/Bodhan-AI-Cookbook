"""Reference passing engine and Indic script guard for Bodhan Task Agent.

Guarantees:
1. Reference passing: every tool result is stored under an ID like @stepN.field (e.g. @step1.translated_text).
2. The LLM sees a safe data preview in <tool_data>...</tool_data> and passes @stepN.field as arguments.
3. Indic Script Guard: strictly rejects any tool argument containing Indian-language characters
   that was directly authored by the LLM (not part of a reference or file path).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Unicode code point blocks for Indian scripts & Perso-Arabic (Urdu, Kashmiri, Sindhi)
INDIC_UNICODE_RANGES = [
    (0x0600, 0x06FF),  # Arabic (Urdu, Kashmiri, Sindhi)
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
    (
        0x0900,
        0x097F,
    ),  # Devanagari (Hindi, Marathi, Sanskrit, Nepali, Konkani, Bodo, Dogri, Maithili, Sindhi)
    (0x0980, 0x09FF),  # Bengali & Assamese & Manipuri
    (0x0A00, 0x0A7F),  # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B00, 0x0B7F),  # Oriya (Odia)
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
    (0x1CD0, 0x1CFF),  # Vedic Extensions
    (0xA8E0, 0xA8FF),  # Devanagari Extended
    (0x1C50, 0x1C7F),  # Ol Chiki (Santali)
    (0xABC0, 0xABFF),  # Meetei Mayek (Manipuri)
    (0xAAE0, 0xAAFF),  # Meetei Mayek Extensions
]


def contains_indic_script(text: str) -> bool:
    """Returns True if string contains any character from Indian-language Unicode blocks."""
    if not isinstance(text, str):
        return False
    for ch in text:
        cp = ord(ch)
        for start, end in INDIC_UNICODE_RANGES:
            if start <= cp <= end:
                return True
    return False


def check_indic_guard(args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Checks that tool arguments do NOT contain raw Indic-script characters written by the LLM.

    The LLM must only pass English text or reference pointers (e.g. '@step1.text').
    Returns:
        (is_valid, error_message)
    """
    for key, value in args.items():
        if isinstance(value, str):
            if contains_indic_script(value):
                return False, (
                    f"POLICY VIOLATION: Indic text authored by LLM rejected in argument '{key}'. "
                    "The LLM must NEVER write or author Indian-language text directly. "
                    "You may write English and ask indic_translate to translate it, or pass references like @step1.text."
                )
        elif isinstance(value, dict):
            ok, err = check_indic_guard(value)
            if not ok:
                return False, err
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and contains_indic_script(item):
                    return False, (
                        f"POLICY VIOLATION: Indic text authored by LLM rejected in argument '{key}'. "
                        "The LLM must NEVER write or author Indian-language text directly."
                    )
    return True, None


class ReferenceStore:
    """Stores intermediate tool execution outputs and resolves references like @step1.text."""

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._step_records: Dict[int, Dict[str, Any]] = {}

    def store_result(
        self,
        step_num: int,
        tool_name: str,
        result_data: Dict[str, Any],
        success: bool = True,
    ) -> None:
        """Indexes result fields under @stepN.<field> and @stepN.<tool>.<field>."""
        prefix = f"@step{step_num}"
        self._store[prefix] = result_data

        # Store individual top-level fields
        for field, val in result_data.items():
            self._store[f"{prefix}.{field}"] = val
            self._store[f"@{tool_name}.{field}"] = val

        # Common aliases
        if "text" in result_data:
            self._store[f"{prefix}.text"] = result_data["text"]
        if "translated_text" in result_data:
            self._store[f"{prefix}.translated_text"] = result_data["translated_text"]
            self._store[f"{prefix}.text"] = result_data["translated_text"]
        if "full_text" in result_data:
            self._store[f"{prefix}.full_text"] = result_data["full_text"]
            self._store[f"{prefix}.text"] = result_data["full_text"]
        if "output_file_path" in result_data:
            self._store[f"{prefix}.output_file_path"] = result_data["output_file_path"]
            self._store[f"{prefix}.path"] = result_data["output_file_path"]

        self._step_records[step_num] = {
            "step": step_num,
            "tool": tool_name,
            "success": success and result_data.get("success", True) is not False,
            "fields": set(result_data.keys()),
        }

    def validate_and_resolve_arguments(
        self,
        current_step: int,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Strictly validates and resolves @stepN.field references in tool arguments.

        Blocks the call before dispatch if:
        1. Reference points to current or future step (step >= current_step).
        2. Reference points to an unexecuted step.
        3. Reference points to a failed step.
        4. Reference points to a missing field in that step's output.
        5. Any '@step' token remains unresolved after resolution.
        """
        # 1. Scan for all @step tokens in arguments before resolution (recursively in dicts, lists, JSON strings)
        step_pattern = re.compile(r"@step(\d+)(?:\.([a-zA-Z0-9_]+))?")

        def extract_refs(obj: Any) -> List[Tuple[int, Optional[str]]]:
            found: List[Tuple[int, Optional[str]]] = []
            if isinstance(obj, str):
                stripped = obj.strip()
                if (stripped.startswith("{") and stripped.endswith("}")) or (
                    stripped.startswith("[") and stripped.endswith("]")
                ):
                    try:
                        parsed = json.loads(stripped)
                        found.extend(extract_refs(parsed))
                    except Exception:
                        pass
                for match in step_pattern.finditer(obj):
                    found.append((int(match.group(1)), match.group(2)))
            elif isinstance(obj, dict):
                for v in obj.values():
                    found.extend(extract_refs(v))
            elif isinstance(obj, list):
                for item in obj:
                    found.extend(extract_refs(item))
            return found

        for ref_step, ref_field in extract_refs(arguments):
            if ref_step >= current_step:
                return arguments, (
                    f"Reference error: '@step{ref_step}' refers to current or future step {ref_step} "
                    f"(currently executing step {current_step}). You must execute prerequisite tools before "
                    f"referencing their outputs."
                )

            if ref_step not in self._step_records:
                return arguments, (
                    f"Reference error: step {ref_step} has not been executed yet. Prerequisite tool must be run first."
                )

            rec = self._step_records[ref_step]
            if not rec["success"]:
                return arguments, (
                    f"Reference error: step {ref_step} ({rec['tool']}) failed and produced no usable output."
                )

            if ref_field:
                valid_fields = rec["fields"] | {
                    "text",
                    "path",
                    "translated_text",
                    "output_file_path",
                    "full_text",
                }
                if (
                    ref_field not in valid_fields
                    and f"@step{ref_step}.{ref_field}" not in self._store
                ):
                    avail = sorted(list(rec["fields"]))
                    return arguments, (
                        f"Reference error: field '{ref_field}' does not exist in step {ref_step} output. "
                        f"Available fields: {avail}"
                    )

        # 2. Perform resolution
        resolved = self.resolve_arguments(arguments)

        # 3. Check for any remaining '@step' token after resolution
        def contains_step_token(obj: Any) -> Optional[str]:
            if isinstance(obj, str):
                if "@step" in obj:
                    return obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    found = contains_step_token(v)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = contains_step_token(item)
                    if found:
                        return found
            return None

        residual = contains_step_token(resolved)
        if residual:
            return (
                arguments,
                f"Reference error: unresolved '@step' reference found after resolution: '{residual}'",
            )

        return resolved, None

    def get_value(self, ref_key: str) -> Optional[Any]:
        """Resolves a single reference string such as '@step1.text'."""
        key = ref_key.strip()
        if not key.startswith("@"):
            return None
        return self._store.get(key)

    def resolve_value(self, val: Any) -> Any:
        """Recursively substitutes @stepN references in values."""
        if isinstance(val, str):
            val_strip = val.strip()
            # Exact match: entire string is a reference
            if val_strip.startswith("@") and val_strip in self._store:
                return self._store[val_strip]

            # Check if val is a JSON-encoded string
            if (val_strip.startswith("{") and val_strip.endswith("}")) or (
                val_strip.startswith("[") and val_strip.endswith("]")
            ):
                try:
                    parsed = json.loads(val_strip)
                    resolved_parsed = self.resolve_value(parsed)
                    if resolved_parsed != parsed:
                        return resolved_parsed
                except Exception:
                    pass

            # In-string substitutions: replace @stepN.field within string
            pattern = re.compile(r"(@step\d+(?:\.[a-zA-Z0-9_]+)?)")

            def replace_match(m):
                ref = m.group(1)
                resolved = self._store.get(ref)
                if resolved is not None:
                    if isinstance(resolved, (dict, list)):
                        return json.dumps(resolved, ensure_ascii=False)
                    return str(resolved)
                return ref

            return pattern.sub(replace_match, val)

        if isinstance(val, dict):
            return {k: self.resolve_value(v) for k, v in val.items()}

        if isinstance(val, list):
            return [self.resolve_value(item) for item in val]

        return val

    def resolve_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Substitutes references in all tool arguments."""
        return {k: self.resolve_value(v) for k, v in arguments.items()}

    def format_preview_data_for_llm(
        self, step_num: int, tool_name: str, result_data: Dict[str, Any]
    ) -> str:
        """Formats tool results for the LLM wrapped in <tool_data>...</tool_data> with reference handles."""
        primary_text_keys = ("full_text", "text", "content", "translated_text", "transcript")
        noisy_meta_keys = (
            "pages",
            "chunks",
            "segments",
            "cached_pages_count",
            "billed_pages_count",
            "cached_chunks_count",
            "billed_chunks_count",
        )

        summary_lines = [f'<tool_data step="{step_num}" tool="{tool_name}">']
        has_english_primary = False
        shown_english_values: Set[str] = set()

        ordered_keys: List[str] = []
        for k in result_data:
            if k not in primary_text_keys and k not in noisy_meta_keys:
                ordered_keys.append(k)
        for k in primary_text_keys:
            if k in result_data:
                ordered_keys.append(k)
        for k in noisy_meta_keys:
            if k in result_data:
                ordered_keys.append(k)

        for k in ordered_keys:
            v = result_data[k]
            ref_handle = f"@step{step_num}.{k}"
            if isinstance(v, str):
                if contains_indic_script(v):
                    preview = v[:25].replace("\n", " ")
                    summary_lines.append(
                        f'  {k}: "{preview}..." [REFERENCE: pass {ref_handle}, length={len(v)} chars]'
                    )
                elif k in primary_text_keys:
                    has_english_primary = True
                    if v in shown_english_values:
                        summary_lines.append(
                            f"  {k}: [same text; REFERENCE: pass {ref_handle}, length={len(v)} chars]"
                        )
                    else:
                        shown_english_values.add(v)
                        if len(v) <= 1200:
                            summary_lines.append(
                                f'  {k}: "{v}" [REFERENCE: {ref_handle}, length={len(v)} chars]'
                            )
                        else:
                            head = v[:850]
                            tail = v[-350:]
                            summary_lines.append(
                                f'  {k}: "{head}\n... [middle omitted; full {len(v)} chars in {ref_handle}] ...\n{tail}" '
                                f"[REFERENCE: pass {ref_handle}, length={len(v)} chars]"
                            )
                else:
                    if len(v) > 100:
                        preview = v[:95].replace("\n", " ")
                        summary_lines.append(
                            f'  {k}: "{preview}..." [REFERENCE: pass {ref_handle}, length={len(v)} chars]'
                        )
                    else:
                        summary_lines.append(f'  {k}: "{v}" [REFERENCE: {ref_handle}]')
            elif isinstance(v, (int, float, bool)):
                summary_lines.append(f"  {k}: {v} [REFERENCE: {ref_handle}]")
            elif isinstance(v, list):
                summary_lines.append(f"  {k}: [list of {len(v)} items] [REFERENCE: {ref_handle}]")
            elif v is None:
                summary_lines.append(f"  {k}: null")
            else:
                summary_lines.append(f"  {k}: [data] [REFERENCE: {ref_handle}]")

        summary_lines.append("</tool_data>")
        output_str = "\n".join(summary_lines)
        max_len = 1800 if has_english_primary else 500
        if len(output_str) > max_len:
            head = output_str[: max_len - 35]
            output_str = head + "\n  ... [truncated]\n</tool_data>"

        # Always append available references line at the very end
        avail_refs: List[str] = []
        # Check primary text reference
        primary_text = (
            result_data.get("text")
            or result_data.get("translated_text")
            or result_data.get("full_text")
            or result_data.get("transcript")
        )
        if isinstance(primary_text, str):
            avail_refs.append(f"@step{step_num}.text ({len(primary_text)} chars)")

        for k, v in result_data.items():
            if k == "text":
                continue
            ref_name = f"@step{step_num}.{k}"
            if isinstance(v, str):
                avail_refs.append(f"{ref_name} ({len(v)} chars)")
            elif isinstance(v, (list, dict)):
                avail_refs.append(f"{ref_name} ({len(v)} items)")
            else:
                avail_refs.append(ref_name)

        ref_line = "Available references: " + ", ".join(avail_refs)
        return f"{output_str}\n{ref_line}"

    def format_compact_stub_for_llm(
        self, step_num: int, tool_name: str, result_data: Dict[str, Any]
    ) -> str:
        """Compact reference-only stub (~180 chars) used to compact older large tool outputs in LLM history."""
        primary_key = None
        primary_val = ""
        for k in ("translated_text", "full_text", "text", "content", "transcript"):
            val = result_data.get(k)
            if isinstance(val, str) and val:
                primary_key = k
                primary_val = val
                break

        success = result_data.get("success", True)
        lines = [f'<tool_data step="{step_num}" tool="{tool_name}">', f"  success: {success}"]
        avail_refs: List[str] = []
        if primary_key:
            preview_len = 25 if contains_indic_script(primary_val) else 60
            snippet = primary_val[:preview_len].replace("\n", " ")
            lines.append(
                f'  {primary_key}: "{snippet}..." [compacted; {len(primary_val)} chars; pass @step{step_num}.{primary_key}]'
            )
            avail_refs.append(f"@step{step_num}.text ({len(primary_val)} chars)")
            if primary_key != "text":
                avail_refs.append(f"@step{step_num}.{primary_key} ({len(primary_val)} chars)")
        out_path = result_data.get("output_file_path") or result_data.get("file_path")
        if isinstance(out_path, str) and out_path:
            lines.append(f'  output_file_path: "{out_path}"')
            avail_refs.append(f"@step{step_num}.output_file_path")
        lines.append("</tool_data>")
        ref_line = "Available references: " + (
            ", ".join(avail_refs) if avail_refs else f"@step{step_num}"
        )
        return "\n".join(lines) + f"\n{ref_line}"


class TextProvenanceRegistry:
    """Tracks SHA-256 hashes and normalized forms of texts produced by Bodhan tools in this run,
    or read from files inside the allowed files directory."""

    def __init__(self, files_dir: Optional[Path] = None):
        self.files_dir = Path(files_dir).resolve() if files_dir else None
        self._registered_texts: Set[str] = set()
        self._registered_hashes: Set[str] = set()

    def register_text(self, text: str) -> None:
        if not text or not isinstance(text, str):
            return
        clean = text.strip()
        if not clean:
            return
        self._registered_texts.add(clean)
        h = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        self._registered_hashes.add(h)

    def is_registered(self, text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        clean = text.strip()
        if not clean:
            return False
        if clean in self._registered_texts:
            return True
        h = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        if h in self._registered_hashes:
            return True
        # Check files in files_dir for exact match
        if self.files_dir and self.files_dir.exists():
            for f in self.files_dir.glob("**/*"):
                if f.is_file():
                    try:
                        fc = f.read_text(encoding="utf-8", errors="ignore").strip()
                        if fc and fc == clean:
                            self.register_text(fc)
                            return True
                    except Exception:
                        pass
        return False


def validate_tool_call_provenance(
    tool_name: str,
    raw_args: Dict[str, Any],
    resolved_args: Dict[str, Any],
    registry: TextProvenanceRegistry,
    files_dir: Optional[Path] = None,
) -> Tuple[bool, Optional[str]]:
    """Validates that:
    1. indic_speak accepts text ONLY from an @ref, a file in --files, or literal text when lang is 'en'.
    2. indic_translate literal text must be English-only (rejects Indic/Perso-Arabic script).
    """
    if tool_name == "indic_speak":
        lang = str(resolved_args.get("language") or resolved_args.get("lang", "")).lower().strip()
        raw_text = raw_args.get("input_text") or raw_args.get("input") or raw_args.get("text") or ""
        resolved_text = str(
            resolved_args.get("input_text")
            or resolved_args.get("input")
            or resolved_args.get("text")
            or ""
        )

        # If language is English, literal text authored by LLM is permitted
        if lang in ("en", "english"):
            return True, None

        # Non-English: must come from @ref or a file in files_dir
        if isinstance(raw_text, str) and raw_text.strip().startswith("@"):
            return True, None

        # Check if raw_text is a path to a file in files_dir
        if files_dir:
            try:
                p = Path(raw_text.strip())
                if not p.is_absolute():
                    p = (files_dir / p).resolve()
                if p.exists() and p.is_file():
                    return True, None
            except Exception:
                pass

        # If literal text was passed, verify it matches registered tool outputs
        if registry.is_registered(resolved_text):
            return True, None

        return False, (
            f"POLICY VIOLATION: indic_speak for non-English language '{lang}' only accepts text originating "
            "from an @ref or a file in --files. Literal text authored or transliterated by the LLM is prohibited. "
            "To speak non-English text, obtain it via indic_translate or indic_ocr and pass the reference (e.g. @step1.translated_text)."
        )

    if tool_name == "indic_translate":
        raw_text = raw_args.get("text") or raw_args.get("input") or ""
        if isinstance(raw_text, str) and not raw_text.strip().startswith("@"):
            if files_dir:
                try:
                    p = Path(raw_text.strip())
                    if not p.is_absolute():
                        p = (files_dir / p).resolve()
                    if p.exists() and p.is_file():
                        return True, None
                except Exception:
                    pass
            if contains_indic_script(raw_text):
                return False, (
                    "POLICY VIOLATION: Literal text sent to indic_translate must be English-only. "
                    "The LLM must NEVER write Indian-language text. To translate Indic text, use a reference "
                    "(@stepN.field) from OCR, transcribe, or an input file."
                )

    return True, None


def sanitize_final_answer(
    answer: str,
    registry: TextProvenanceRegistry,
    artifacts_dir: Optional[Path] = None,
    raw_answer: Optional[str] = None,
) -> str:
    """Guards the agent's final answer.

    Indic-script text in the LLM-written explanation is kept only if:
    1. The whole line exactly equals a registered tool output, or
    2. Came from a resolved @stepN reference.
    Anything else is replaced with '(see Deliverables)'.
    Pointers must use relative paths (artifacts/...), never absolute paths.
    """
    if not contains_indic_script(answer):
        return answer

    lines = answer.split("\n")
    raw_lines = raw_answer.split("\n") if raw_answer is not None else []
    sanitized_lines = []
    replacement = "(see Deliverables)"

    for idx, line in enumerate(lines):
        if contains_indic_script(line):
            clean_line = line.strip()
            # 1. Whole line exactly equals a registered tool output
            if registry.is_registered(clean_line):
                sanitized_lines.append(line)
                continue

            # 2. Came from a resolved @stepN reference
            is_resolved_ref = False
            if idx < len(raw_lines):
                raw_line = raw_lines[idx]
                if "@step" in raw_line and not contains_indic_script(raw_line):
                    for reg in registry._registered_texts:
                        if reg in line:
                            remainder = line.replace(reg, "")
                            if not contains_indic_script(remainder):
                                is_resolved_ref = True
                                break

            if is_resolved_ref:
                sanitized_lines.append(line)
            else:
                sanitized_lines.append(replacement)
        else:
            sanitized_lines.append(line)

    return "\n".join(sanitized_lines)
