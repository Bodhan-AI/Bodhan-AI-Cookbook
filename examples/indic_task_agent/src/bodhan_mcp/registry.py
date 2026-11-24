"""Tool registry shared by the MCP server and the in-process agent.

A tool is a pydantic input model + an async handler returning a JSON-able dict.
`invoke` is the single entry point: it validates arguments, runs the handler,
and converts every failure into a structured error, so bad input can never
crash a client.
"""

from __future__ import annotations

import copy
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from . import errors as E
from .runtime import COST_SCOPE, RunContext

Handler = Callable[[RunContext, Any], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    read_only: bool = False
    paid: bool = False  # calls a billed Bodhan model
    output_schema: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)

    def input_schema(self) -> dict[str, Any]:
        return clean_schema(self.input_model.model_json_schema())


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make pydantic schemas portable for MCP clients and OpenAI-style function
    calling: inline $refs, drop titles, collapse Optional[X] to X (not required)."""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                merged = {**walk(copy.deepcopy(defs[ref])), **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged)
            if "anyOf" in node:
                options = [o for o in node["anyOf"] if o.get("type") != "null"]
                if len(options) == 1:
                    rest = {k: v for k, v in node.items() if k != "anyOf"}
                    return walk({**options[0], **rest})
            out = {}
            for k, v in node.items():
                if k == "title":
                    continue
                if k == "default" and v is None:
                    continue
                out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned


def _validation_message(exc: ValidationError) -> tuple[str, list[dict[str, Any]]]:
    problems = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(arguments)"
        problems.append({"field": loc, "problem": err.get("msg", "invalid")})
    text = "; ".join(f"{p['field']}: {p['problem']}" for p in problems[:8])
    return f"Invalid arguments: {text}", problems


async def invoke(spec: ToolSpec, ctx: RunContext, arguments: dict[str, Any] | None,
                 scope: list[float] | None = None) -> tuple[dict[str, Any], bool]:
    """Run a tool. Returns (payload, is_error). Never raises."""
    t0 = time.monotonic()
    # `scope` collects every amount this call commits; a caller that may cancel the call passes its own
    # list so the spend stays attributable after cancellation.
    scope = scope if scope is not None else []
    token = COST_SCOPE.set(scope)
    try:
        if arguments is not None and not isinstance(arguments, dict):
            raise E.ToolError(E.INVALID_ARGUMENT, "Arguments must be a JSON object.")
        # Many models send null for an optional argument they mean to leave out; treat it as "not given"
        # (a required argument sent as null is still reported as missing).
        given = {k: v for k, v in (arguments or {}).items() if v is not None}
        try:
            args = spec.input_model.model_validate(given)
        except ValidationError as exc:
            msg, problems = _validation_message(exc)
            raise E.ToolError(E.INVALID_ARGUMENT, msg, hint=f"See the input schema of {spec.name}.", details={"problems": problems}) from exc
        result = await spec.handler(ctx, args)
        result = {"ok": True, **result}
        is_error = False
    except E.ToolError as exc:
        result = {"ok": False, "error": exc.as_dict()}
        is_error = True
    except Exception as exc:  # last-resort guard: report, never crash the client
        ctx.emit({"type": "internal_error", "tool": spec.name, "error": repr(exc), "traceback": traceback.format_exc()[-2000:]})
        result = {"ok": False, "error": E.ToolError(E.INTERNAL_ERROR, f"Unexpected error in {spec.name}: {type(exc).__name__}: {exc}",
                                                    hint="This is a bug in the tool; the input may still be valid.").as_dict()}
        is_error = True
    finally:
        COST_SCOPE.reset(token)
    result["cost_inr"] = round(sum(scope), 5)
    result["elapsed_s"] = round(time.monotonic() - t0, 3)
    if ctx.ledger.max_inr is not None:
        result["budget_remaining_inr"] = round(max(ctx.ledger.remaining or 0.0, 0.0), 4)
    return result, is_error
