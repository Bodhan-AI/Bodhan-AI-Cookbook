from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from time import perf_counter
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.schemas.base import ToolOutput
from bodhan_mcp.schemas.telemetry import CallRecord, UsageSummary
from bodhan_mcp.utils.telemetry import call_scope, log_tool_summary, summarize

if TYPE_CHECKING:
    from bodhan_mcp.config import Settings
    from bodhan_mcp.core.client import BodhanClient

logger = logging.getLogger(__name__)

In = TypeVar("In", bound=BaseModel)
Out = TypeVar("Out", bound=ToolOutput)


def _validation_details(exc: ValidationError) -> dict[str, Any]:
    return {"errors": exc.errors(include_input=False, include_context=False, include_url=False)}


class BaseTool(ABC, Generic[In, Out]):
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[ToolOutput]]
    title: ClassVar[str | None] = None
    read_only: ClassVar[bool] = False
    destructive: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    open_world: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        output = cls.__dict__.get("Output")
        if output is not None and not (isinstance(output, type) and issubclass(output, ToolOutput)):
            raise TypeError(f"{cls.__name__}.Output must subclass ToolOutput")

    def __init__(self, client: BodhanClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    @abstractmethod
    async def run(self, params: In) -> Out: ...

    def validate_input(self, params: BaseModel | dict[str, Any]) -> In:
        raw = params.model_dump() if isinstance(params, BaseModel) else params
        try:
            return self.Input.model_validate(raw)  # type: ignore[return-value]
        except ValidationError as exc:
            raise BodhanToolError(
                "invalid_input",
                f"Invalid arguments for '{self.name}'.",
                hint="Fix the fields listed in details.errors and call again.",
                details=_validation_details(exc),
            ) from exc

    def validate_output(self, result: BaseModel, usage: UsageSummary | None) -> Out:
        try:
            data = result.model_dump()
            data["usage"] = usage.model_dump() if usage is not None else None
            return self.Output.model_validate(data)  # type: ignore[return-value]
        except (ValidationError, AttributeError) as exc:
            logger.error("tool=%s produced invalid output: %s", self.name, exc)
            raise BodhanToolError("internal_error", f"Tool '{self.name}' produced an invalid result.") from exc

    def _usage(self, calls: list[CallRecord], start: float) -> UsageSummary | None:
        try:
            return summarize(calls, (perf_counter() - start) * 1000)
        except Exception:
            logger.exception("tool=%s usage summary unavailable", self.name)
            return None

    def _fail(self, error: BodhanToolError, calls: list[CallRecord], start: float) -> BodhanToolError:
        usage = self._usage(calls, start)
        error.details["usage"] = usage.model_dump() if usage is not None else None
        log_tool_summary(self.name, f"{error.status}:{error.code}", usage)
        return error

    async def execute(self, params: BaseModel | dict[str, Any]) -> Out:
        start = perf_counter()
        with call_scope() as calls:
            try:
                validated = self.validate_input(params)
                result = await self.run(validated)
                usage = self._usage(calls, start)
                output = self.validate_output(result, usage)
            except BodhanToolError as error:
                raise self._fail(error, calls, start) from error.__cause__
            except Exception as exc:
                logger.exception("tool=%s crashed", self.name)
                crash = BodhanToolError("internal_error", f"Tool '{self.name}' failed unexpectedly.")
                raise self._fail(crash, calls, start) from exc
        log_tool_summary(self.name, "ok", usage)
        return output
