from __future__ import annotations

import errno
import logging
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path

from bodhan_mcp.exceptions import BodhanToolError

logger = logging.getLogger(__name__)

_MAX_UTF8_BYTES_PER_CHAR = 4
_UTF8_BOM_BYTES = 3
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_LINK_UNSUPPORTED = {errno.EPERM, errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK}


def _roots_hint(roots: Iterable[Path], env_var: str) -> str:
    listed = ", ".join(str(r) for r in roots)
    return f"Use a path inside: {listed}. Other folders can be allowed with {env_var}."


def _matching_root(path: Path, roots: Iterable[Path]) -> Path | None:
    for root in roots:
        if path == root or path.is_relative_to(root):
            return root
    return None


def _has_hidden_part(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _hidden_error(raw: str, purpose: str) -> BodhanToolError:
    return BodhanToolError(
        "path_not_allowed",
        f"Hidden files and folders cannot be used as the {purpose}.",
        details={"path": raw},
    )


def _resolve_within(raw: str, base: Path, roots: tuple[Path, ...], purpose: str, env_var: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise BodhanToolError("invalid_input", f"The {purpose} path is empty or invalid.")
    raw = raw.strip()
    roots = tuple(root.expanduser().resolve() for root in roots)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base.expanduser().resolve() / candidate

    lexical = Path(os.path.normpath(candidate))
    lexical_root = _matching_root(lexical, roots)
    if lexical_root is not None and _has_hidden_part(lexical, lexical_root):
        raise _hidden_error(raw, purpose)

    resolved = candidate.resolve()
    root = _matching_root(resolved, roots)
    if root is None:
        raise BodhanToolError(
            "path_not_allowed",
            f"The {purpose} path is outside the folders this server may access.",
            hint=_roots_hint(roots, env_var),
            details={"path": raw},
        )
    if _has_hidden_part(resolved, root):
        raise _hidden_error(raw, purpose)
    return resolved


def resolve_input_file(raw: str, input_dir: Path, readable_roots: tuple[Path, ...]) -> Path:
    path = _resolve_within(raw, input_dir, readable_roots, "input", "BODHAN_MCP_ALLOWED_DIRS")
    if not path.exists():
        raise BodhanToolError(
            "file_not_found",
            f"No file at '{raw.strip()}'.",
            hint="Check the path; relative paths are resolved against the server's input folder.",
            details={"path": raw.strip()},
        )
    if not path.is_file():
        raise BodhanToolError("invalid_input", f"'{raw.strip()}' is not a regular file.", details={"path": raw.strip()})
    return path


def resolve_output_file(raw: str, output_dir: Path, writable_roots: tuple[Path, ...]) -> Path:
    path = _resolve_within(raw, output_dir, writable_roots, "output", "BODHAN_MCP_OUTPUT_DIR")
    if path.is_dir():
        raise BodhanToolError(
            "invalid_input",
            f"'{raw.strip()}' is a directory, not a file.",
            details={"path": raw.strip()},
        )
    return path


def read_text_file(path: Path, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    max_bytes = max_chars * _MAX_UTF8_BYTES_PER_CHAR + _UTF8_BOM_BYTES
    try:
        fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BodhanToolError(
                "path_not_allowed", f"{path.name} changed into a symbolic link and was not read."
            ) from exc
        raise BodhanToolError("unsupported_file", f"Could not open {path.name}: {exc.strerror}.") from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise BodhanToolError("invalid_input", f"{path.name} is not a regular file.")
        try:
            data = handle.read(max_bytes + 1)
        except OSError as exc:
            raise BodhanToolError("unsupported_file", f"Could not read {path.name}: {exc.strerror}.") from exc
    if len(data) > max_bytes:
        raise BodhanToolError(
            "input_too_large",
            f"{path.name} is over the {max_chars}-character input limit.",
            hint="Split the file or raise BODHAN_MAX_INPUT_CHARS.",
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BodhanToolError(
            "unsupported_encoding",
            f"{path.name} is not valid UTF-8 text (byte {exc.start}).",
            hint="Save the file as UTF-8 and try again.",
        ) from exc
    if len(text) > max_chars:
        raise BodhanToolError(
            "input_too_large",
            f"{path.name} has {len(text)} characters, over the {max_chars}-character limit.",
            hint="Split the file or raise BODHAN_MAX_INPUT_CHARS.",
            details={"chars": len(text)},
        )
    return text


def _publish_no_replace(tmp: Path, dest: Path) -> None:
    try:
        os.link(tmp, dest)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in _LINK_UNSUPPORTED:
            raise BodhanToolError(
                "internal_error",
                "The output folder's filesystem cannot publish files without risking an overwrite.",
                hint="Pass overwrite=true, or point BODHAN_MCP_OUTPUT_DIR at a local filesystem.",
            ) from exc
        raise


def write_text_atomic(
    path: Path,
    text: str,
    *,
    overwrite: bool = False,
    protected: Iterable[Path] = (),
) -> Path:
    return write_bytes_atomic(path, text.encode("utf-8"), overwrite=overwrite, protected=protected)


def write_pair_atomic(
    main: Path,
    main_text: str,
    sidecar_suffix: str,
    sidecar_text: str,
    *,
    explicit: bool,
    overwrite: bool,
    protected: Iterable[Path] = (),
    max_attempts: int = 100,
) -> tuple[Path, Path]:
    protected = tuple(protected)
    base = main.stem
    attempts = 1 if explicit else max_attempts
    for attempt in range(1, attempts + 1):
        name = base if attempt == 1 else f"{base}-{attempt}"
        target = main.with_name(f"{name}{main.suffix}")
        sidecar = main.with_name(f"{name}{sidecar_suffix}")
        sidecar_existed = sidecar.exists()
        try:
            write_text_atomic(sidecar, sidecar_text, overwrite=overwrite, protected=protected)
        except BodhanToolError as error:
            if error.code == "file_exists" and not explicit:
                continue
            raise
        try:
            write_text_atomic(target, main_text, overwrite=overwrite, protected=protected)
        except BaseException as error:
            if not sidecar_existed:
                try:
                    sidecar.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning("could not remove %s after a failed write: %s", sidecar.name, cleanup_error)
            if isinstance(error, BodhanToolError) and error.code == "file_exists" and not explicit:
                continue
            raise
        return target, sidecar
    raise BodhanToolError(
        "file_exists",
        f"Could not find a free file name for {main.name} in the output folder.",
        hint="Pass an explicit output_path or clean up the output folder.",
    )


def write_bytes_atomic(
    path: Path,
    data: bytes,
    *,
    overwrite: bool = False,
    protected: Iterable[Path] = (),
) -> Path:
    if any(path == p.resolve() for p in protected):
        raise BodhanToolError(
            "invalid_input",
            "The output path is the same as an input file.",
            hint="Choose a different output_path.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            _publish_no_replace(tmp, path)
    except FileExistsError as exc:
        raise BodhanToolError(
            "file_exists",
            f"{path.name} already exists.",
            hint="Pass overwrite=true or choose a different output_path.",
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return path
