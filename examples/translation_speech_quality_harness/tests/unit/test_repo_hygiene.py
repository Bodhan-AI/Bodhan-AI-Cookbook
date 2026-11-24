"""Secrets never reach the repository (hard rule 6). Runs in every ``make check``.

Checks the files git tracks or would add (not ignored): ``.env`` is never among them, no tracked file
contains any value from the local ``.env``, and no tracked file contains a key-shaped string.
Skipped where there is no git checkout (e.g. inside the Docker build context).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY_SHAPED = re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{20,}")  # OpenAI/OpenRouter/Bodhan-style keys
SECRET_VARS = re.compile(r"^\s*([A-Z_]*(?:KEY|TOKEN|SECRET)[A-Z_]*)\s*=\s*(\S+)\s*$")


def repo_files() -> list[Path]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p]


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binary or unreadable: not a text secret carrier we can scan


def local_secret_values() -> list[str]:
    env = ROOT / ".env"
    if not env.is_file():
        return []
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        m = SECRET_VARS.match(line)
        if m and len(m.group(2)) >= 12:
            values.append(m.group(2))
    return values


def test_dotenv_is_never_tracked() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in repo_files()}
    assert not any(n == ".env" or n.endswith("/.env") or re.search(r"(^|/)\.env\.(?!example$)", n) for n in names)


def test_no_local_secret_value_in_any_repo_file() -> None:
    secrets = local_secret_values()
    if not secrets:
        pytest.skip("no local .env secrets to look for")
    leaks = [
        p.relative_to(ROOT).as_posix()
        for p in repo_files()
        if (t := text_of(p)) is not None and any(s in t for s in secrets)
    ]
    assert leaks == [], "a value from .env appears in these files (values not shown)"


def test_no_key_shaped_strings_in_repo_files() -> None:
    hits = [
        p.relative_to(ROOT).as_posix() for p in repo_files() if (t := text_of(p)) is not None and KEY_SHAPED.search(t)
    ]
    assert hits == [], "key-shaped strings found (values not shown)"
