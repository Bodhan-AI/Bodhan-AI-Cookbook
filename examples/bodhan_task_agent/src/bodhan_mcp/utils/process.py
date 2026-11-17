from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

from bodhan_mcp.exceptions import BodhanToolError

_STDERR_TAIL_CHARS = 300


def remaining_time(deadline: float, label: str) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise out_of_time(label)
    return remaining


def out_of_time(label: str) -> BodhanToolError:
    return BodhanToolError(
        "tool_deadline_exceeded",
        f"{label} ran out of time before the tool deadline.",
        hint="Try a smaller input or raise BODHAN_TOOL_DEADLINE_S.",
        retryable=True,
    )


def stderr_tail(stderr: bytes, path: Path) -> str:
    text = stderr.decode("utf-8", errors="replace").replace(str(path), path.name)
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][-_STDERR_TAIL_CHARS:] if lines else ""


async def run_process(args: list[str], deadline: float, label: str) -> tuple[int, bytes, bytes]:
    timeout_s = remaining_time(deadline, label)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise BodhanToolError(
            "internal_error",
            f"{label} failed: {Path(args[0]).name} could not be run.",
            hint="Install ffmpeg and poppler-utils (pdfinfo, pdftoppm) and restart the server.",
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if isinstance(exc, asyncio.TimeoutError):
            raise out_of_time(label) from exc
        raise
    return proc.returncode or 0, stdout, stderr
