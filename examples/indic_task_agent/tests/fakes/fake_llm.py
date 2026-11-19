"""A scripted stand-in for the LLM, so the end-to-end tests are deterministic and offline.

A script is a list of functions. Each receives `seen` (tool name -> last parsed tool result) and
returns a list of (tool_name, arguments) calls for that turn. The first `complete()` call (the
forced submit_plan) returns `plan`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bodhan_agent.llm import LLMReply, ToolCall

Turn = Callable[[dict[str, Any]], list[tuple[str, dict[str, Any]]]]


class ScriptedLLM:
    model = "scripted-test-llm"

    def __init__(self, plan: dict[str, Any], turns: list[Turn]) -> None:
        self.plan = plan
        self.turns = list(turns)
        self.calls = 0
        self.ids: dict[str, str] = {}
        self.seen: dict[str, Any] = {}
        self.history: list[list[dict[str, Any]]] = []
        self.tools_offered: list[list[str]] = []

    def _absorb(self, messages: list[dict[str, Any]]) -> None:
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id") in self.ids:
                try:
                    self.seen[self.ids[m["tool_call_id"]]] = json.loads(m["content"])
                except (json.JSONDecodeError, TypeError):
                    self.seen[self.ids[m["tool_call_id"]]] = m["content"]

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], force_tool: str | None = None
    ) -> LLMReply:
        self.calls += 1
        self.history.append(messages)
        self.tools_offered.append([t["function"]["name"] for t in tools])
        self._absorb(messages)
        if force_tool == "submit_plan":
            return self._reply([("submit_plan", self.plan)])
        if not self.turns:
            return self._reply(
                [("finish", {"status": "failed", "answer_en": "Script ran out of turns.", "artifacts": []})]
            )
        turn = self.turns.pop(0)
        return self._reply(turn(self.seen))

    def _reply(self, calls: list[tuple[str, dict[str, Any]]]) -> LLMReply:
        out = []
        for name, args in calls:
            cid = f"call_{self.calls}_{len(self.ids)}"
            self.ids[cid] = name
            out.append(ToolCall(cid, name, args, json.dumps(args, ensure_ascii=False)))
        return LLMReply(content="", tool_calls=out, usage={"prompt_tokens": 100, "completion_tokens": 20})


def result(seen: dict[str, Any], tool: str) -> dict[str, Any]:
    """The parsed (un-wrapped) result of the last call to `tool`."""
    value = seen[tool]
    return value.get("result", value) if isinstance(value, dict) else value
