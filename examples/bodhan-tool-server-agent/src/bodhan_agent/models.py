"""Pydantic schemas for Bodhan Task Agent.

Defines schemas for:
- Task planning and decomposition
- Step trace logs and execution state
- Output verification checks
- Budget tracking and constraints
- Run reports and artifact manifests
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Execution status for the task run."""
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    IMPOSSIBLE = "IMPOSSIBLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    FAILED = "FAILED"


class PlanStep(BaseModel):
    """Individual action step in an execution plan."""
    step_number: int = Field(..., description="1-indexed step number")
    description: str = Field(..., description="Action description")
    tool: str = Field(..., description="Target Bodhan tool name")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Typed arguments for tool call")
    expected_output_type: str = Field("text", description="Expected result artifact type")


class Plan(BaseModel):
    """Decomposed plan for fulfilling a multi-step language task."""
    task_summary: str = Field(..., description="English summary of the planned approach")
    source_language: Optional[str] = Field(None, description="Identified source language")
    target_language: Optional[str] = Field(None, description="Identified target language")
    is_feasible: bool = Field(True, description="Whether the task is achievable given Bodhan limits")
    impossibility_reason: Optional[str] = Field(None, description="Honest explanation if task is impossible")
    steps: List[PlanStep] = Field(default_factory=list, description="Ordered execution steps")


class VerificationResult(BaseModel):
    """Outcome of an output verification test."""
    check_name: str = Field(..., description="Name of the verification check")
    passed: bool = Field(..., description="Whether output passed the check")
    details: str = Field(..., description="Diagnostic details or explanation")
    score: Optional[float] = Field(None, description="Confidence or similarity metric if applicable")


class StepTrace(BaseModel):
    """Detailed audit trace for a single step execution."""
    step_number: int
    tool: str
    arguments: Dict[str, Any]
    status: str
    output: Optional[Dict[str, Any]] = None
    cost_inr: float = 0.0
    duration_seconds: float = 0.0
    verification_passed: bool = True
    verification_notes: Optional[str] = None
    timestamp: str


class BudgetState(BaseModel):
    """Current state of resource and cost budgets."""
    max_steps: int
    steps_taken: int = 0
    max_cost_inr: float
    current_cost_inr: float = 0.0
    timeout_seconds: float
    elapsed_seconds: float = 0.0
    cost_breakdown: Dict[str, float] = Field(default_factory=dict)


class RunReport(BaseModel):
    """Comprehensive machine-readable report for an agent task run."""
    task: str
    status: ExecutionStatus
    reason: Optional[str] = None
    steps_taken: int
    max_steps: int
    total_cost_inr: float
    max_cost_inr: float
    cost_breakdown: Dict[str, float]
    duration_seconds: float
    input_files: List[str]
    artifacts: List[str]
    verification_results: List[VerificationResult]
    plan: Optional[Plan] = None
    final_summary: Optional[str] = None
