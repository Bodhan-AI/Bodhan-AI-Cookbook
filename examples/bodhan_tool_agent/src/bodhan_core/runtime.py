"""Runtime: everything a tool needs (client, ledger, cache, output folders), built once per process/run."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .cache import Cache
from .client import BodhanClient
from .config import Settings, default_work_dir
from .errors import ToolError
from .ledger import CostLedger


@dataclass
class Runtime:
    settings: Settings
    client: BodhanClient
    ledger: CostLedger
    cache: Cache
    work_dir: Path

    @property
    def artifacts_dir(self) -> Path:
        d = self.work_dir / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def create(
        cls,
        work_dir: Path | None = None,
        max_cost_inr: float | None = None,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> "Runtime":
        settings = settings or Settings.from_env()
        work_dir = (work_dir or default_work_dir()).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        if max_cost_inr is None and os.environ.get("BODHAN_MAX_COST_INR"):
            max_cost_inr = float(os.environ["BODHAN_MAX_COST_INR"])
        return cls(settings, BodhanClient(settings, transport), CostLedger(max_cost_inr), Cache(work_dir / ".cache"), work_dir)

    # ---- file helpers shared by tools -------------------------------------------------
    def resolve_input(self, path: str) -> Path:
        if not path or not str(path).strip():
            raise ToolError("invalid_input", "No file path given.", "Pass the path of an existing input or artifact file.")
        p = Path(path).expanduser()
        if not p.is_absolute() and not p.exists():
            alt = self.work_dir / p
            p = alt if alt.exists() else p
        if not p.exists():
            raise ToolError("file_not_found", f"File not found: {path}", "Check the path; inputs are listed in the task manifest.")
        if not p.is_file():
            raise ToolError("not_a_file", f"Not a file: {path}", "Pass a file, not a directory.")
        return p.resolve()

    def artifact_path(self, name: str, default_stem: str, suffix: str) -> Path:
        """A safe path inside artifacts/. Never lets a caller escape the folder."""
        stem = re.sub(r"[^\w.\-]+", "_", Path(name or default_stem).name).strip("._") or default_stem
        if not Path(stem).suffix:
            stem += suffix
        p = self.artifacts_dir / stem
        i = 1
        while p.exists():  # never silently overwrite another artifact
            p = self.artifacts_dir / f"{Path(stem).stem}_{i}{Path(stem).suffix}"
            i += 1
        return p

    def rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.work_dir)).replace("\\", "/")
        except ValueError:
            return str(p)
