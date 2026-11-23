"""Pydantic data models for agent specifications, steps, execution plans, and traces."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


class TaskStatus(str, Enum):
    INITIALIZED = "initialized"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class InputFileInfo(BaseModel):
    path: str
    filename: str
    file_type: Literal["audio", "image", "document", "text", "other"]
    format: str
    size_bytes: int
    duration_seconds: float | None = None
    page_count: int | None = None
    checksum_sha256: str
    prompt_injection_warning: bool = False


class TaskSpec(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    user_prompt: str
    detected_target_languages: list[str] = Field(default_factory=list)
    detected_source_languages: list[str] = Field(default_factory=list)
    output_format: str = "text"
    requires_summary: bool = False
    requires_speech: bool = False
    requires_ocr: bool = False
    requires_translation: bool = False
    requires_subtitles: bool = False
    input_files: list[InputFileInfo] = Field(default_factory=list)


class PlanStep(BaseModel):
    step_id: str
    description: str
    tool_name: Literal[
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "extract_text_from_document",
        "llm_summarize",
        "format_subtitles",
        "verify_artifact",
    ]
    tool_arguments: dict[str, Any]
    dependencies: list[str] = Field(default_factory=list)
    expected_output_artifact: str | None = None
    estimated_cost_inr: float = 0.0
    status: StepStatus = StepStatus.PENDING
    result_data: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
    duration_ms: float = 0.0


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    task_id: str
    steps: list[PlanStep]
    estimated_total_cost_inr: float = 0.0
    max_steps: int = 30
    max_cost_inr: float = 20.0
    created_at: float = Field(default_factory=time.time)


class VerificationResult(BaseModel):
    check_name: str
    status: VerificationStatus
    details: str
    target_artifact: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    task_id: str
    step_id: str | None = None
    event_type: str  # task_start, step_start, step_complete, step_retry, verification, error, task_end
    tool_name: str | None = None
    sanitized_arguments: dict[str, Any] | None = None
    result_summary: str | None = None
    status: str
    latency_ms: float = 0.0
    estimated_cost_inr: float = 0.0
    actual_cost_inr: float | None = None
    retry_count: int = 0
    verification_result: str | None = None
