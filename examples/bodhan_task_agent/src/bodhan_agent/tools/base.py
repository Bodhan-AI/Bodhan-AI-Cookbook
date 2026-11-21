from __future__ import annotations

import copy
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ValidationError

from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, ArtifactKind, Ledger, Producer
from bodhan_agent.schemas.plan import Plan
from bodhan_agent.schemas.tools import LocalResult
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.files import write_bytes_atomic

if TYPE_CHECKING:
    from bodhan_agent.clients.mcp_session import McpSession
    from bodhan_agent.core.budget import BudgetManager

META_FIELDS = ("reason", "plan_step")
REASON_FIELD: dict[str, Any] = {
    "type": "string",
    "description": "Why you are making this call, in one short English sentence.",
}
PLAN_STEP_FIELD: dict[str, Any] = {
    "type": "string",
    "description": "Id of the plan step this call serves, such as S2. Optional.",
}


class LocalToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True)
class ToolContext:
    settings: RunSettings
    ledger: Ledger
    mcp: McpSession | None = None
    budget: BudgetManager | None = None
    deadline: float | None = None
    seq: int | None = None
    plan: Plan | None = None


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline(copy.deepcopy(defs[ref.removeprefix("#/$defs/")]), defs)
        return {key: _inline(value, defs) for key, value in node.items() if key not in ("$defs", "title")}
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _inline(copy.deepcopy(schema), schema.get("$defs", {}))


def with_meta(parameters: dict[str, Any]) -> dict[str, Any]:
    schema = inline_schema(parameters)
    properties = {key: value for key, value in schema.get("properties", {}).items() if key not in META_FIELDS}
    required = [key for key in schema.get("required", []) if key not in META_FIELDS]
    schema.update(
        type="object",
        properties={"reason": REASON_FIELD, **properties, "plan_step": PLAN_STEP_FIELD},
        required=["reason", *required],
    )
    return schema


def function_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": with_meta(parameters)},
    }


def split_meta(arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
    clean = {key: value for key, value in arguments.items() if key not in META_FIELDS}
    reason = arguments.get("reason")
    plan_step = arguments.get("plan_step")
    return (
        clean,
        reason.strip() or None if isinstance(reason, str) else None,
        plan_step.strip() or None if isinstance(plan_step, str) else None,
    )


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def known_file(ctx: ToolContext, raw: str) -> Artifact:
    path = Path(raw.strip())
    if not path.is_absolute():
        raise LocalToolError(
            "invalid_input",
            f"'{raw}' is not an absolute path.",
            hint="Use the absolute path shown in the inputs or in an earlier tool result.",
        )
    artifact = ctx.ledger.by_path(path)
    if artifact is None:
        raise LocalToolError(
            "path_not_allowed",
            f"'{raw}' is not a file of this run.",
            hint="Only input files and files produced by earlier tool calls can be used.",
        )
    if not artifact.usable:
        raise LocalToolError(
            "artifact_not_usable",
            f"{artifact.id} has status '{artifact.step_status}'.",
            hint="Use a verified file, or redo the step that produced this one.",
        )
    if not Path(artifact.path).is_file():
        raise LocalToolError("file_not_found", f"{artifact.id} ({artifact.path}) no longer exists.")
    if artifact.sha256 is not None and file_digest(Path(artifact.path))[0] != artifact.sha256:
        raise LocalToolError(
            "file_changed",
            f"{artifact.id} changed on disk after it was registered and will not be used.",
            hint="Produce the file again with the tool that made it.",
        )
    return artifact


def output_file(ctx: ToolContext, raw: str | None, default_name: str, suffixes: tuple[str, ...]) -> Path:
    name = (raw or "").strip() or default_name
    if guards.contains_indic(name):
        raise LocalToolError("indic_text", "File names you choose must be in English letters.")
    candidate = Path(name)
    if not candidate.is_absolute():
        candidate = ctx.settings.work_dir / candidate
    resolved = candidate.resolve()
    roots = (ctx.settings.work_dir.resolve(), ctx.settings.artifacts_dir.resolve())
    root = next((item for item in roots if resolved.is_relative_to(item)), None)
    if root is None:
        raise LocalToolError(
            "path_not_allowed",
            f"'{name}' is outside the run's work and artifacts folders.",
            hint="Give a plain file name; it is saved in the work folder.",
        )
    if any(part.startswith(".") for part in resolved.relative_to(root).parts):
        raise LocalToolError("path_not_allowed", "Hidden files and folders cannot be written.")
    if suffixes and resolved.suffix.lower() not in suffixes:
        raise LocalToolError("invalid_input", f"'{name}' must end in {' or '.join(suffixes)}.")
    if resolved.exists() or ctx.ledger.by_path(resolved) is not None:
        raise LocalToolError("file_exists", f"'{resolved.name}' already exists.", hint="Choose a different file name.")
    return resolved


def write_new(path: Path, data: bytes) -> Path:
    try:
        return write_bytes_atomic(path, data, overwrite=False)
    except BodhanToolError as error:
        raise LocalToolError(error.code, error.message, hint="Choose a different file name.") from error
    except OSError as exc:
        raise LocalToolError("write_failed", f"{path.name} could not be written: {exc.strerror}.") from exc


def register(
    ctx: ToolContext,
    path: Path,
    kind: ArtifactKind,
    producer: Producer,
    sources: list[str] | None = None,
    tool: str | None = None,
    **facts: Any,
) -> Artifact:
    digest, size = file_digest(path)
    return ctx.ledger.add(
        Artifact(
            id=ctx.ledger.next_id("produced"),
            path=str(path),
            kind=kind,
            producer=producer,
            tool=tool,
            sources=sources or [],
            created_seq=ctx.seq,
            sha256=digest,
            size_bytes=size,
            **facts,
        )
    )


class LocalTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[BaseModel]]

    def definition(self) -> dict[str, Any]:
        return function_tool(self.name, self.description, self.Input.model_json_schema())

    def parse(self, arguments: dict[str, Any]) -> BaseModel:
        try:
            return self.Input.model_validate(arguments)
        except ValidationError as exc:
            raise LocalToolError(
                "invalid_input",
                f"Invalid arguments for '{self.name}'.",
                hint="Fix the fields listed in details.errors and call again.",
                details={"errors": exc.errors(include_input=False, include_context=False, include_url=False)},
            ) from exc

    @abstractmethod
    async def run(self, params: Any, ctx: ToolContext) -> LocalResult: ...

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> LocalResult:
        return await self.run(self.parse(arguments), ctx)
