"""Artifact store, CaMeL meta/content splitter, and Indic provenance guard."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from bodhan_agent.models import ProvenanceRecord
from bodhan_mcp.cache import sha256_bytes, sha256_text
from bodhan_mcp.text_utils import contains_indic_script

# Keys stripped from any tool response before anything can reach P-LLM context.
CONTENT_STRIP_KEYS = frozenset({"preview", "text", "translation", "transliteration", "content", "raw_text"})

# Trusted provenance sources allowed to contain Indian-language script text.
TRUSTED_INDIC_SOURCES = frozenset(
    {
        "bodhan:indic-transcribe",
        "bodhan:indic-translate",
        "bodhan:indic-transliterate",
        "bodhan:indic-ocr",
        "input_file",
        "deterministic:subtitles",
        "deterministic:merge",
    }
)


class ProvenanceViolationError(RuntimeError):
    """Raised if Indian-language text lacks verified Bodhan or input-file provenance."""


class ArtifactStore:
    """Tracks every step output handle, file path, and provenance record.

    Enforces the CaMeL split:
      - `step_meta[step_id]` stores ONLY typed metadata + paths/handles (safe for P-LLM)
      - Untrusted text/media stays on disk and is referenced via `$step_id.field`
      - Every Bodhan Indic text output is recorded so deliverables can be provenance-verified
    """

    def __init__(self, out_dir: Path, allowed_input_paths: list[Path]) -> None:
        self.out_dir = out_dir.resolve()
        self.artifacts_dir = self.out_dir / "artifacts"
        self.work_dir = self.out_dir / ".work"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.allowed_inputs: set[Path] = {p.resolve() for p in allowed_input_paths}
        self.step_meta: dict[str, dict[str, Any]] = {}
        self.provenance_by_path: dict[str, ProvenanceRecord] = {}
        self.provenance_by_handle: dict[str, ProvenanceRecord] = {}
        # Raw byte-identical Indic snippets produced by Bodhan or present in input files.
        self._verified_indic_chunks: list[str] = []

        # Register original input text files as verified input provenance.
        for p in self.allowed_inputs:
            if p.exists() and p.is_file():
                try:
                    raw = p.read_bytes()
                    rec = ProvenanceRecord(
                        handle=f"input:{p.name}",
                        path=str(p),
                        source="input_file",
                        sha256=sha256_bytes(raw),
                    )
                    self.provenance_by_path[str(p)] = rec
                    if len(raw) <= 500_000:
                        try:
                            txt = raw.decode("utf-8")
                            if contains_indic_script(txt):
                                self._verified_indic_chunks.append(txt)
                        except UnicodeDecodeError:
                            pass
                except OSError:
                    pass

    @staticmethod
    def sanitize_tool_response_for_planner(resp: dict[str, Any]) -> dict[str, Any]:
        """Strip any untrusted text preview or content fields before P-LLM or trace sees it."""
        def _clean(obj: Any) -> Any:
            if isinstance(obj, dict):
                out: dict[str, Any] = {}
                for k, v in obj.items():
                    if k in CONTENT_STRIP_KEYS:
                        continue
                    out[k] = _clean(v)
                return out
            if isinstance(obj, list):
                return [_clean(item) for item in obj]
            return obj

        return _clean(resp)

    def register_step_output(
        self,
        step_id: str,
        tool: str,
        resp: dict[str, Any],
        *,
        parent_handles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Store sanitized metadata for `step_id` and record provenance of any file artifacts."""
        sanitized = self.sanitize_tool_response_for_planner(resp)
        data = dict(sanitized.get("data") or {})
        meta: dict[str, Any] = {
            "ok": bool(sanitized.get("ok")),
            "cost_inr": float(sanitized.get("cost_inr", 0.0)),
            "cache_hit": bool(sanitized.get("cache_hit", False)),
            "model": sanitized.get("model"),
            **data,
        }
        if sanitized.get("error"):
            meta["error"] = sanitized["error"]
        self.step_meta[step_id] = meta

        # Map tool -> provenance source label.
        model = sanitized.get("model")
        if model:
            source = f"bodhan:{model}"
        elif tool.startswith("q_"):
            source = "q_llm:en"
        elif tool == "write_subtitles":
            source = "deterministic:subtitles"
        elif tool == "merge_text_artifacts":
            source = "deterministic:merge"
        else:
            source = f"tool:{tool}"

        if tool == "publish_artifact":
            # publish_artifact already preserved the original Bodhan/input provenance record on dest.
            return meta

        for field_name in ("text_path", "audio_path", "subtitle_path", "segments_path", "chunk_manifest_path", "artifact_path"):
            val = data.get(field_name)
            if isinstance(val, str) and Path(val).is_file():
                p = Path(val).resolve()
                handle = f"${step_id}.{field_name}"
                raw = p.read_bytes()
                rec = ProvenanceRecord(
                    handle=handle,
                    path=str(p),
                    source=source,
                    model=model,
                    language=data.get("target_language") or data.get("language"),
                    sha256=sha256_bytes(raw),
                    parent_handles=list(parent_handles or []),
                )
                self.provenance_by_path[str(p)] = rec
                self.provenance_by_handle[handle] = rec
                if source in TRUSTED_INDIC_SOURCES and field_name in ("text_path", "subtitle_path", "artifact_path"):
                    try:
                        txt = raw.decode("utf-8")
                        if contains_indic_script(txt):
                            self._verified_indic_chunks.append(txt)
                    except UnicodeDecodeError:
                        pass

        return meta

    def resolve_ref(self, value: Any) -> Any:
        """Resolve `$step_id.field` strings into stored metadata values/paths."""
        if isinstance(value, str) and value.startswith("$"):
            body = value[1:]
            if "." not in body:
                raise KeyError(f"Reference '{value}' must have the form '$step_id.field'.")
            step_id, field_name = body.split(".", 1)
            if step_id not in self.step_meta:
                raise KeyError(f"Unknown step reference '{step_id}' in '{value}'.")
            step_data = self.step_meta[step_id]
            if field_name not in step_data:
                raise KeyError(f"Step '{step_id}' has no field '{field_name}'. Available: {sorted(step_data.keys())}")
            return step_data[field_name]
        if isinstance(value, list):
            return [self.resolve_ref(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve_ref(v) for k, v in value.items()}
        return value

    def check_path_in_read_scope(self, path_str: str) -> bool:
        """Ensure an input file path is either in --files or a registered artifact in out_dir."""
        p = Path(path_str).expanduser().resolve()
        if p in self.allowed_inputs:
            return True
        if str(p) in self.provenance_by_path:
            return True
        try:
            p.relative_to(self.out_dir)
            return True
        except ValueError:
            return False

    def safe_artifact_destination(self, filename: str) -> Path:
        """Confine deliverable output paths strictly inside `out/artifacts/`."""
        clean_name = Path(filename).name
        if not clean_name or clean_name in (".", "..") or "/" in filename or "\\" in filename:
            raise ValueError(f"Output filename '{filename}' escapes artifacts directory.")
        dest = (self.artifacts_dir / clean_name).resolve()
        dest.relative_to(self.artifacts_dir)  # Raises ValueError on traversal.
        return dest

    def assert_indic_provenance(self, path: Path) -> None:
        """Enforce hard rule: any Indic-script text in a deliverable must come from Bodhan or input files."""
        resolved = path.resolve()
        rec = self.provenance_by_path.get(str(resolved))
        # Binary files (MP3/WAV) are verified via their provenance record.
        if resolved.suffix.lower() in (".mp3", ".wav", ".flac", ".ogg"):
            if rec and rec.source != "bodhan:indic-speak":
                raise ProvenanceViolationError(
                    f"Audio deliverable {resolved.name} has non-Bodhan provenance '{rec.source}'."
                )
            return

        try:
            text = resolved.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return

        if not contains_indic_script(text):
            return

        if rec is None:
            raise ProvenanceViolationError(
                f"Deliverable '{resolved.name}' contains Indic-script text with no recorded provenance."
            )
        if rec.source not in TRUSTED_INDIC_SOURCES:
            raise ProvenanceViolationError(
                f"Deliverable '{resolved.name}' contains Indic-script text from forbidden source '{rec.source}'."
            )
        # Verify byte-level lineage: every non-empty Indic line must appear verbatim in verified Bodhan/input chunks.
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or not contains_indic_script(stripped):
                continue
            if not any(stripped in chunk for chunk in self._verified_indic_chunks):
                raise ProvenanceViolationError(
                    f"Deliverable '{resolved.name}' contains Indic text not matching any Bodhan tool output."
                )

    def publish_artifact(self, source_path: str, filename: str) -> tuple[Path, ProvenanceRecord | None]:
        """Validate provenance and copy a step artifact into `out/artifacts/<filename>`."""
        src = Path(source_path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Artifact source not found: {source_path}")
        if not self.check_path_in_read_scope(str(src)):
            raise PermissionError(f"Source path outside task scope: {source_path}")

        self.assert_indic_provenance(src)
        dest = self.safe_artifact_destination(filename)
        if src != dest:
            shutil.copy2(src, dest)
        src_rec = self.provenance_by_path.get(str(src))
        if src_rec is not None:
            copied_rec = src_rec.model_copy(update={"path": str(dest)})
            self.provenance_by_path[str(dest)] = copied_rec
            return dest, copied_rec
        return dest, None

    def write_trusted_text_artifact(
        self,
        text: str,
        filename: str,
        *,
        source: str,
        parent_handles: list[str] | None = None,
    ) -> Path:
        """Write a text file into `.work/` with provenance tracking (rejects LLM-authored Indic text)."""
        if source.startswith(("q_llm", "p_llm")) and contains_indic_script(text):
            raise ProvenanceViolationError(
                f"LLM source '{source}' attempted to write Indian-language text; only Bodhan models may produce Indic text."
            )
        dest = (self.work_dir / Path(filename).name).resolve()
        dest.write_text(text, encoding="utf-8")
        rec = ProvenanceRecord(
            handle=f"work:{dest.name}",
            path=str(dest),
            source=source,
            sha256=sha256_text(text),
            parent_handles=list(parent_handles or []),
        )
        self.provenance_by_path[str(dest)] = rec
        if source in TRUSTED_INDIC_SOURCES and contains_indic_script(text):
            self._verified_indic_chunks.append(text)
        return dest
