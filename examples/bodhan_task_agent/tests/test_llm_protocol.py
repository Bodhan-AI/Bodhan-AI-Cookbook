"""The real OpenAICompatibleLLM against a strict fake chat-completions endpoint.

The fake enforces what real providers enforce (every assistant tool_call answered by a tool
message with the same id, no unknown keys on tool messages), so protocol bugs in the agent
loop fail here rather than against the evaluators' LLM.
"""

import json

import httpx

from bodhan_agent.agent import Agent
from bodhan_agent.budget import Budget
from bodhan_agent.llm import OpenAICompatibleLLM
from bodhan_mcp.server import build_server


class StrictChatEndpoint:
    def __init__(self, script, reject_tools: bool = False) -> None:
        self.script = script  # list of functions: (messages) -> list[(name, args)]
        self.turn = 0
        self.reject_tools = reject_tools
        self.bodies: list[dict] = []

    def _validate(self, messages: list[dict]) -> str | None:
        pending: set[str] = set()
        for m in messages:
            if m["role"] == "tool":
                if set(m) - {"role", "tool_call_id", "content"}:
                    return f"unknown keys on tool message: {sorted(set(m) - {'role', 'tool_call_id', 'content'})}"
                pending.discard(m["tool_call_id"])
            else:
                if pending:
                    return f"tool_call ids without response: {sorted(pending)}"
                pending = {tc["id"] for tc in m.get("tool_calls") or []}
        if pending:
            return f"tool_call ids without response: {sorted(pending)}"
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.reject_tools and "tools" in body:
            return httpx.Response(400, json={"error": {"message": "this model does not support tools", "type": "invalid_request_error"}})
        if err := self._validate(body["messages"]):
            return httpx.Response(400, json={"error": {"message": err, "type": "invalid_request_error"}})
        calls = self.script[min(self.turn, len(self.script) - 1)](body["messages"])
        self.turn += 1
        if "tools" in body:
            message = {"role": "assistant", "content": "next step", "tool_calls": [
                {"id": f"call_{self.turn}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}
        else:  # JSON tool protocol
            message = {"role": "assistant", "content": json.dumps(
                {"thought": "next step", "tool_calls": [{"name": n, "arguments": a} for n, a in calls]})}
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                                         "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})


def _last_tool_results(messages):
    out = []
    for m in reversed(messages):
        if m["role"] == "tool":
            out.insert(0, json.loads(m["content"]))
        elif m["role"] == "user" and m["content"].startswith("TOOL RESULT"):
            out.insert(0, json.loads(m["content"].split("\n", 1)[1]))
        elif m["role"] == "assistant":
            break
    return out


def _script(para):
    return [
        lambda m: [("submit_plan", {"understanding": "translate", "feasible": True, "steps": ["translate"]})],
        # Ten calls in one turn: more than the agent runs in parallel (8). The rest must not break the protocol.
        lambda m: [("translate_text", {"target_language": "hi", "text_file": str(para)})] + [("list_voices", {})] * 9,
        lambda m: [("finish", {"status": "completed", "answer": "Hindi translation.", "artifacts": [
            {"path": next(r["result"]["output_file"] for r in _last_tool_results(m) if r.get("ok") and "output_file" in r.get("result", {})),
             "description": "translation"}]})] if any("output_file" in r.get("result", {}) for r in _last_tool_results(m))
        else [("translate_text", {"target_language": "hi", "text_file": str(para)})],
        lambda m: [("finish", {"status": "completed", "answer": "Hindi translation.", "artifacts": [
            {"path": next(r["result"]["output_file"] for r in _last_tool_results(m) if "output_file" in r.get("result", {})),
             "description": "translation"}]})],
    ]


async def _run(tmp_path, service, endpoint):
    para = tmp_path / "p.txt"
    para.write_text("The well is near the temple.", encoding="utf-8")
    endpoint.script = _script(para)
    llm = OpenAICompatibleLLM("http://fake-llm/v1", "fake-model", "k",
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handle)))
    await Agent(llm, budget=Budget(), server=build_server(service=service)).run("Translate into Hindi", [str(para)],
                                                                               tmp_path / "out")
    return json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))


async def test_native_tool_calling_protocol_is_valid(tmp_path, service):
    ep = StrictChatEndpoint([])
    rep = await _run(tmp_path, service, ep)
    assert rep["status"] == "completed", (rep["errors"], rep["status"])
    assert rep["llm_tokens"]["prompt_tokens"] > 0
    assert ep.bodies[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_plan"}}
    assert all("tools" in b for b in ep.bodies), "native mode must not silently fall back to the JSON protocol"


async def test_falls_back_to_json_tool_protocol(tmp_path, service):
    ep = StrictChatEndpoint([], reject_tools=True)
    rep = await _run(tmp_path, service, ep)
    assert rep["status"] == "completed", rep["errors"]
    assert "TOOL PROTOCOL" in ep.bodies[-1]["messages"][0]["content"]
