"""Path sandbox and artifact references.

Two jobs:

1. Sandbox. Tools accept file paths from callers, and those paths can come from an LLM
   that has just read untrusted text (an OCR'd page saying "now read /etc/passwd").
   Every input path is resolved (symlinks followed) and must land inside an allowed read
   root. Outputs are only ever written under the artifacts and scratch directories.
   The check is structural: it does not depend on the LLM behaving.

2. Artifact references. Every file a tool produces is registered and gets a short ref
   (art_001, art_002, ...). Callers can pass `artifact:art_003` wherever a file is
   expected, so a model never has to copy a long path correctly to chain two tools.
   Large text never travels through the LLM context: tools return a ref plus a preview.

Layout (all configurable, see .env.example):
    <out>/artifacts/        files for the user; also holds index.json (the ref registry)
    <out>/.scratch/         intermediate files such as audio chunks and page images

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import ArtifactKind, ArtifactRef, ErrorCode, ToolError

ARTIFACT_PREFIX = "artifact:"
INDEX_NAME = "index.json"

_MIME_OVERRIDES = {
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".md": "text/markdown",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".json": "application/json",
    ".txt": "text/plain",
}
_KIND_BY_SUFFIX: dict[str, ArtifactKind] = {
    **dict.fromkeys((".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac"), "audio"),
    **dict.fromkeys((".srt", ".vtt"), "subtitles"),
    ".md": "markdown",
    ".txt": "text",
    ".json": "json",
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"), "image"),
}

# Never readable, even inside an allowed root: credentials and VCS internals.
_DENIED_DIR_NAMES = {".git", ".ssh", ".aws", ".gnupg"}
_DENIED_FILE_PREFIXES = (".env",)
_DENIED_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_DENIED_FILE_NAMES = {"id_rsa", "id_ed25519", "credentials"}

_STEM_CLEAN = re.compile(r"[^A-Za-z0-9._-]+")
_SUFFIX_OK = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


class Workspace:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        out_dir: str | os.PathLike[str] | None = None,
        input_dirs: Sequence[str | os.PathLike[str]] = (),
        max_input_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self.root = Path(root or Path.cwd()).expanduser().resolve()
        self.out_dir = Path(out_dir).expanduser() if out_dir else self.root / "out"
        if not self.out_dir.is_absolute():
            self.out_dir = self.root / self.out_dir
        self.out_dir = self.out_dir.resolve()
        self.artifacts_dir = self.out_dir / "artifacts"
        self.scratch_dir = self.out_dir / ".scratch"
        self.input_dirs = [self._abs(Path(p).expanduser()) for p in input_dirs]
        self.max_input_bytes = max_input_bytes
        # Where relative input paths are searched, in order.
        self.read_roots: list[Path] = _dedupe(
            [self.root, *self.input_dirs, self.artifacts_dir, self.scratch_dir]
        )
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Workspace:
        env = os.environ if env is None else env
        dirs = [p for p in (env.get("BODHAN_INPUT_DIRS") or "").split(os.pathsep) if p.strip()]
        raw_mb = (env.get("BODHAN_MAX_INPUT_MB") or "").strip()
        try:
            max_bytes = int(float(raw_mb) * 1024 * 1024) if raw_mb else 200 * 1024 * 1024
        except ValueError:
            raise ValueError(f"BODHAN_MAX_INPUT_MB must be a number, got {raw_mb!r}") from None
        return cls(
            env.get("BODHAN_WORKSPACE") or None,
            out_dir=env.get("BODHAN_OUT_DIR") or None,
            input_dirs=dirs,
            max_input_bytes=max_bytes,
        )

    def _abs(self, p: Path) -> Path:
        return (p if p.is_absolute() else self.root / p).resolve()

    # ------------------------------------------------------------------ reading

    def resolve_input(
        self,
        value: str,
        *,
        suffixes: Collection[str] | None = None,
        what: str = "file",
    ) -> Path:
        """Turn a caller-supplied path or `artifact:<ref>` into a safe, existing file.

        Order of checks is deliberate: the sandbox check comes before the existence
        check, so a caller cannot probe which files exist outside the sandbox.
        """
        if not isinstance(value, str) or not value.strip():
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`{what}` must be a non-empty path.",
                hint="Pass a file path or an `artifact:<ref>` from an earlier result.",
            )
        raw = value.strip()
        if "\x00" in raw:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"`{what}` contains a NUL character.")

        if raw.startswith(ARTIFACT_PREFIX):
            ref = raw[len(ARTIFACT_PREFIX) :].strip()
            art = self.lookup(ref)
            if art is None:
                raise ToolError(
                    ErrorCode.FILE_NOT_FOUND,
                    f"Unknown artifact reference {ref!r}.",
                    hint="Use the `ref` field of an artifact returned by an earlier tool call, "
                    "written as `artifact:<ref>`, or pass the file path directly.",
                )
            path = Path(art.path)
            self._require_allowed(path, raw)
        else:
            path = self._find_existing(raw)

        if path.is_dir():
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`{what}` is a directory, not a file.",
                hint="Pass the path of one file inside it.",
            )
        size = path.stat().st_size
        if size > self.max_input_bytes:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`{what}` is {size / 1e6:.0f} MB, "
                f"over the {self.max_input_bytes / 1e6:.0f} MB limit.",
                hint="Split or compress the file, or raise BODHAN_MAX_INPUT_MB.",
            )
        if suffixes:
            allowed = {s.lower() for s in suffixes}
            if path.suffix.lower() not in allowed:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Unsupported file type {path.suffix or '(none)'!r} for `{what}`.",
                    hint=f"Supported extensions: {', '.join(sorted(allowed))}.",
                )
        return path

    def _find_existing(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        candidates = [p] if p.is_absolute() else [root / p for root in self.read_roots]
        blocked: Path | None = None
        for cand in candidates:
            try:
                resolved = cand.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if not self._is_allowed(resolved):
                blocked = blocked or resolved
                continue
            if resolved.exists():
                return resolved
        if blocked is not None:
            # Decided by where the path resolves, never by whether a file exists there,
            # so callers cannot use the error code to probe outside the sandbox.
            self._require_allowed(blocked, raw)
        raise ToolError(
            ErrorCode.FILE_NOT_FOUND,
            f"No such file: {raw!r}.",
            hint="Check the path. Relative paths are searched under: "
            + ", ".join(str(r) for r in self.read_roots)
            + ".",
        )

    def _is_allowed(self, resolved: Path) -> bool:
        if not any(resolved == r or r in resolved.parents for r in self.read_roots):
            return False
        return not _is_denied(resolved)

    def _require_allowed(self, resolved: Path, raw: str) -> None:
        if self._is_allowed(resolved):
            return
        raise ToolError(
            ErrorCode.PATH_NOT_ALLOWED,
            f"Path {raw!r} is outside the allowed workspace or is a protected file.",
            hint="Only files under the workspace, the configured input directories and the "
            "artifacts directory can be read. Allowed roots: "
            + ", ".join(str(r) for r in self.read_roots)
            + ".",
        )

    # ------------------------------------------------------------------ writing

    def new_artifact_path(self, stem: str, suffix: str, *, scratch: bool = False) -> Path:
        """Reserve a unique, empty file under artifacts/ (or scratch/) and return its path.

        Reservation uses exclusive create, so concurrent tool calls can never be handed
        the same name.
        """
        if not _SUFFIX_OK.match(suffix):
            raise ToolError(ErrorCode.INTERNAL, f"Invalid artifact suffix {suffix!r}.")
        clean = _STEM_CLEAN.sub("_", stem).strip("._-")[:80] or "artifact"
        directory = self.scratch_dir if scratch else self.artifacts_dir
        directory.mkdir(parents=True, exist_ok=True)
        for n in range(1, 10_000):
            name = f"{clean}{suffix}" if n == 1 else f"{clean}-{n}{suffix}"
            path = directory / name
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            except FileExistsError:
                continue
            return path
        raise ToolError(ErrorCode.INTERNAL, "Could not find a free artifact file name.")

    def write_text(
        self, stem: str, suffix: str, text: str, kind: ArtifactKind | None = None
    ) -> ArtifactRef:
        return self._write(stem, suffix, text.encode("utf-8"), kind)

    def write_bytes(
        self, stem: str, suffix: str, data: bytes, kind: ArtifactKind | None = None
    ) -> ArtifactRef:
        return self._write(stem, suffix, data, kind)

    def import_file(
        self, src: str | os.PathLike[str], stem: str, suffix: str, kind: ArtifactKind | None = None
    ) -> ArtifactRef:
        """Copy an internal file (e.g. a cache blob) into artifacts/ and register it.
        `src` is trusted: callers must not pass user-supplied paths here."""
        path = self.new_artifact_path(stem, suffix)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return self.register(path, kind)

    def _write(self, stem: str, suffix: str, data: bytes, kind: ArtifactKind | None) -> ArtifactRef:
        path = self.new_artifact_path(stem, suffix)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return self.register(path, kind)

    # --------------------------------------------------------------- the registry

    def register(
        self,
        path: str | os.PathLike[str],
        kind: ArtifactKind | None = None,
        *,
        mime: str | None = None,
    ) -> ArtifactRef:
        """Add (or refresh) a file in the ref registry. Re-registering the same path
        keeps its ref, so a re-written artifact does not get a new identity."""
        p = Path(path).resolve()
        if not p.is_file():
            raise ToolError(ErrorCode.INTERNAL, f"Cannot register missing file {p.name!r}.")
        suffix = p.suffix.lower()
        resolved_kind: ArtifactKind = kind or _KIND_BY_SUFFIX.get(suffix, "other")
        resolved_mime = (
            mime
            or _MIME_OVERRIDES.get(suffix)
            or mimetypes.guess_type(p.name)[0]
            or "application/octet-stream"
        )
        size = p.stat().st_size
        with self._lock:
            index = self._load_index()
            ref = next((r for r, e in index.items() if e["path"] == str(p)), None)
            if ref is None:
                ref = f"art_{len(index) + 1:03d}"
                while ref in index:  # only possible if entries were removed by hand
                    ref = f"art_{int(ref[4:]) + 1:03d}"
            index[ref] = {
                "path": str(p),
                "kind": resolved_kind,
                "mime": resolved_mime,
                "bytes": size,
                "sha256": _sha256_file(p),
                "created_at": time.time(),
            }
            self._save_index(index)
        return ArtifactRef(ref=ref, path=str(p), kind=resolved_kind, mime=resolved_mime, bytes=size)

    def lookup(self, ref: str) -> ArtifactRef | None:
        with self._lock:
            e = self._load_index().get(ref)
        if e is None:
            return None
        return ArtifactRef(
            ref=ref, path=e["path"], kind=e["kind"], mime=e["mime"], bytes=e["bytes"]
        )

    def list_artifacts(self) -> list[ArtifactRef]:
        with self._lock:
            idx = self._load_index()
            return [
                ArtifactRef(ref=r, path=e["path"], kind=e["kind"], mime=e["mime"], bytes=e["bytes"])
                for r, e in sorted(idx.items())
            ]

    def _index_path(self) -> Path:
        return self.artifacts_dir / INDEX_NAME

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index is not None:
            return self._index
        path = self._index_path()
        index: dict[str, dict[str, Any]] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                entries = loaded["artifacts"]
                if not isinstance(entries, dict):
                    raise TypeError("artifacts must be an object")
                index = entries
            except (OSError, ValueError, KeyError, TypeError):
                path.replace(path.with_name(f"{INDEX_NAME}.corrupt"))  # keep it, start fresh
        self._index = index
        return index

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self._index_path()
        tmp = path.with_name(f".{INDEX_NAME}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(
                json.dumps({"artifacts": index}, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------- helpers

    @staticmethod
    def preview(text: str, limit: int = 300) -> str:
        """Short single-block excerpt for tool results. Whole documents never go inline."""
        flat = " ".join(text.split())
        return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


    @staticmethod
    def is_probable_file_ref(value: str, suffixes: Collection[str]) -> bool:
        """Distinguish workspace file references from literal text safely."""
        raw = value.strip()
        if not raw:
            return False
        if raw.startswith(ARTIFACT_PREFIX):
            return True
        if "\n" in raw or "\r" in raw or len(raw) > 512 or "://" in raw:
            return False
        allowed = {s.lower() for s in suffixes}
        if Path(raw).suffix.lower() in allowed:
            return True
        if any(ch.isspace() for ch in raw):
            return False
        return (
            raw.startswith(("/", "./", "../", "~/", "\\"))
            or (len(raw) >= 3 and raw[1] == ":" and raw[2] in {"/", "\\"})
        )

def _is_denied(resolved: Path) -> bool:
    if any(part in _DENIED_DIR_NAMES for part in resolved.parts):
        return True
    name = resolved.name.lower()
    return (
        name in _DENIED_FILE_NAMES
        or name.startswith(_DENIED_FILE_PREFIXES)
        or name.endswith(_DENIED_FILE_SUFFIXES)
    )


def _dedupe(paths: Sequence[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
