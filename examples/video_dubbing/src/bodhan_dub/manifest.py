"""
Run manifest manager for bodhan_dub.
Tracks execution checkpoints, stage progress, and segment statuses to support --resume.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class StageStatus(BaseModel):
    stage_name: str
    status: str = "pending"  # pending, running, completed, failed
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error: Optional[str] = None


class RunManifest(BaseModel):
    run_id: str
    input_file: str
    target_langs: List[str]
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    updated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    stages: Dict[str, StageStatus] = Field(default_factory=dict)
    completed_segments: Dict[str, List[str]] = Field(default_factory=dict)  # lang -> [segment_ids]
    segment_hashes: Dict[str, Dict[str, str]] = Field(default_factory=dict)  # lang -> {seg_id: hash}

    def mark_stage_start(self, stage: str) -> None:
        self.stages[stage] = StageStatus(
            stage_name=stage,
            status="running",
            start_time=time.time(),
        )
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def mark_stage_completed(self, stage: str) -> None:
        if stage in self.stages:
            self.stages[stage].status = "completed"
            self.stages[stage].end_time = time.time()
        else:
            self.stages[stage] = StageStatus(
                stage_name=stage,
                status="completed",
                end_time=time.time(),
            )
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def mark_stage_failed(self, stage: str, error: str) -> None:
        if stage in self.stages:
            self.stages[stage].status = "failed"
            self.stages[stage].end_time = time.time()
            self.stages[stage].error = error
        else:
            self.stages[stage] = StageStatus(
                stage_name=stage,
                status="failed",
                end_time=time.time(),
                error=error,
            )
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def is_stage_completed(self, stage: str) -> bool:
        st = self.stages.get(stage)
        return st is not None and st.status == "completed"

    def record_completed_segment(
        self, lang: str, segment_id: str, content_hash: Optional[str] = None
    ) -> None:
        """B15: Records segment completion with optional cryptographic content hash."""
        if lang not in self.completed_segments:
            self.completed_segments[lang] = []
        if segment_id not in self.completed_segments[lang]:
            self.completed_segments[lang].append(segment_id)
        if content_hash:
            if lang not in self.segment_hashes:
                self.segment_hashes[lang] = {}
            self.segment_hashes[lang][segment_id] = content_hash
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def is_segment_completed(
        self, lang: str, segment_id: str, content_hash: Optional[str] = None
    ) -> bool:
        """B15: Verifies segment completion matching content hash."""
        if segment_id not in self.completed_segments.get(lang, []):
            return False
        if content_hash and lang in self.segment_hashes:
            expected = self.segment_hashes[lang].get(segment_id)
            if expected and expected != content_hash:
                return False
        return True

    def save(self, filepath: Path) -> None:
        """Atomically persists manifest to disk."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = filepath.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(self.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp_path.replace(filepath)

    @classmethod
    def load(cls, filepath: Path) -> Optional["RunManifest"]:
        filepath = Path(filepath)
        if not filepath.exists():
            return None
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return cls(**data)
        except Exception:
            return None
