"""Task state: plan steps, files, artifacts, budget tracking."""
from pydantic import BaseModel, Field
from typing import Any, Optional
from enum import Enum
import time


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = {}
    resolved_args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = []
    purpose: str = ""
    expected_output: str = ""
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    result: Optional[dict] = None
    latency_ms: Optional[float] = None
    cost_inr: float = 0.0
    error: Optional[str] = None


class FileInfo(BaseModel):
    path: str
    name: str
    type: str  # audio, image, pdf, text, unknown
    size_bytes: int
    duration_seconds: Optional[float] = None
    page_count: Optional[int] = None
    detected_script: Optional[str] = None
    mime_type: Optional[str] = None


class ArtifactInfo(BaseModel):
    name: str
    path: str
    kind: str  # audio, text, subtitle, image, markdown
    produced_by: str  # step id


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    IMPOSSIBLE = "impossible"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class TaskState(BaseModel):
    task: str
    goal: str = ""
    deliverables: list[str] = []
    assumptions: list[str] = []
    input_text: Optional[str] = None
    files: list[FileInfo] = []
    artifacts: list[ArtifactInfo] = []
    plan: list[PlanStep] = []
    spent_inr: float = 0.0
    steps_used: int = 0
    elapsed_s: float = 0.0
    status: TaskStatus = TaskStatus.RUNNING
    stop_reason: str = ""
    verifications: list[dict] = []
    out_dir: str = ""
    start_time: float = Field(default_factory=time.time)
    replan_count: int = 0

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        for s in self.plan:
            if s.id == step_id:
                return s
        return None

    def step_output(self, step_id: str) -> Optional[dict]:
        """Get the result dict for a completed step."""
        step = self.get_step(step_id)
        if step and step.result:
            return step.result
        return None

    def compact_view(self) -> dict:
        """Compact representation safe to pass to LLM (no media/base64)."""
        return {
            "task": self.task,
            "goal": self.goal,
            "deliverables": self.deliverables,
            "assumptions": self.assumptions,
            "files": [
                {
                    "name": f.name,
                    "type": f.type,
                    "size_bytes": f.size_bytes,
                    "duration_seconds": f.duration_seconds,
                    "page_count": f.page_count,
                    "detected_script": f.detected_script,
                }
                for f in self.files
            ],
            "artifacts": [
                {"name": a.name, "path": a.path, "kind": a.kind, "produced_by": a.produced_by}
                for a in self.artifacts
            ],
            "plan_steps": [
                {
                    "id": s.id,
                    "tool": s.tool,
                    "status": s.status,
                    "purpose": s.purpose,
                    "error": s.error,
                    "cost_inr": s.cost_inr,
                }
                for s in self.plan
            ],
            "spent_inr": self.spent_inr,
            "steps_used": self.steps_used,
            "status": self.status,
        }
