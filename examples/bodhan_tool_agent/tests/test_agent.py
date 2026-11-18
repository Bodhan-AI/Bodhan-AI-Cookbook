"""Tests for Bodhan Task Agent: loop, budget, reference passing, Indic guard, and MCP integration."""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from bodhan_agent.llm import LLMResponse, ScriptedFakeLLMClient, ToolCallItem
from bodhan_agent.loop import AgentLoop
from bodhan_agent.mcp_client import BodhanMcpClient
from bodhan_agent.refs import ReferenceStore, contains_indic_script


class FakeMcpClient:
    """In-memory fake MCP client for agent logic tests."""

    def __init__(self, tool_handlers: Dict[str, Any] = None):
        self.tool_handlers = tool_handlers or {}
        self.calls: List[Dict[str, Any]] = []

    def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool {name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in [
                "indic_translate",
                "indic_ocr",
                "indic_transcribe",
                "indic_speak",
                "inspect_media",
                "read_text_file",
            ]
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]):
        self.calls.append({"name": name, "arguments": arguments})
        if name in self.tool_handlers:
            handler = self.tool_handlers[name]
            if callable(handler):
                return handler(arguments), None
            return handler, None
        return {"success": True, "text": f"Output from {name}", "estimated_cost_inr": 0.001}, None

    async def start(self):
        pass

    async def close(self):
        pass


# ------------------------------------------------------------------------------
# Unit Tests for Guard & Refs
# ------------------------------------------------------------------------------


def test_indic_script_detection():
    """Detects Devanagari, Bengali, Tamil, Telugu, and Ol Chiki."""
    assert contains_indic_script("नमस्ते") is True
    assert contains_indic_script("নমস্কার") is True
    assert contains_indic_script("வணக்கம்") is True
    assert contains_indic_script("హలో") is True
    assert contains_indic_script("Hello world 123! @#$") is False
    assert contains_indic_script("@step1.translated_text") is False


def test_reference_store_resolution():
    """Resolves @stepN references across strings, lists, and dicts."""
    store = ReferenceStore()
    store.store_result(
        step_num=1,
        tool_name="indic_translate",
        result_data={
            "success": True,
            "translated_text": "क्लास नौ बजे शुरू होती है।",
            "estimated_cost_inr": 0.00018,
        },
    )

    args = {
        "text_ref": "@step1.translated_text",
        "alias_ref": "@step1.text",
        "embedded": "Result is: @step1.translated_text",
        "literal": "Just normal text",
    }
    resolved = store.resolve_arguments(args)

    assert resolved["text_ref"] == "क्लास नौ बजे शुरू होती है।"
    assert resolved["alias_ref"] == "क्लास नौ बजे शुरू होती है।"
    assert "क्लास नौ बजे शुरू होती है।" in resolved["embedded"]
    assert resolved["literal"] == "Just normal text"


# ------------------------------------------------------------------------------
# Agent Loop Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_3_step_task_completes(tmp_path: Path):
    """A 3-step workflow (read_text -> translate -> speak) completes with full audit files."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    input_file = files_dir / "notice.txt"
    input_file.write_text("School opens tomorrow at 9 AM.", encoding="utf-8")

    out_dir = tmp_path / "out"

    # Scripted LLM conversation
    llm_script = [
        # Turn 1: LLM generates plan and calls read_text_file
        LLMResponse(
            content="Plan: 1. Read notice.txt. 2. Translate to Hindi. 3. Synthesize speech.",
            tool_calls=[
                ToolCallItem(id="call_1", name="read_text_file", arguments={"path": "notice.txt"})
            ],
        ),
        # Turn 2: LLM calls indic_translate using reference @step1.text
        LLMResponse(
            content="Now translating the text to Hindi.",
            tool_calls=[
                ToolCallItem(
                    id="call_2",
                    name="indic_translate",
                    arguments={"text": "@step1.text", "target_language": "hi"},
                )
            ],
        ),
        # Turn 3: LLM calls indic_speak using reference @step2.text
        LLMResponse(
            content="Synthesizing audio for translated text.",
            tool_calls=[
                ToolCallItem(
                    id="call_3",
                    name="indic_speak",
                    arguments={
                        "input_text": "@step2.text",
                        "voice": "Kavya",
                        "language": "hi",
                        "output_path": "out/artifacts/notice_hi.wav",
                    },
                )
            ],
        ),
        # Turn 4: LLM concludes
        LLMResponse(content="Task completed: synthesized Hindi speech saved to notice_hi.wav."),
    ]

    mcp_handlers = {
        "read_text_file": lambda args: {
            "success": True,
            "text": "School opens tomorrow at 9 AM.",
            "character_count": 31,
            "estimated_cost_inr": 0.0,
        },
        "indic_translate": lambda args: {
            "success": True,
            "translated_text": "स्कूल कल सुबह 9 बजे खुलता है।",
            "estimated_cost_inr": 0.0002,
        },
        "indic_speak": lambda args: {
            "success": True,
            "output_file_path": str(out_dir / "artifacts" / "notice_hi.wav"),
            "duration_seconds": 2.2,
            "verified": True,
            "estimated_cost_inr": 0.015,
        },
    }

    fake_llm = ScriptedFakeLLMClient(llm_script)
    fake_mcp = FakeMcpClient(mcp_handlers)

    agent = AgentLoop(
        task="Translate notice.txt to Hindi and speak it.",
        files_dir=files_dir,
        out_dir=out_dir,
        max_steps=10,
        max_cost_inr=5.0,
        llm_client=fake_llm,
        mcp_client=fake_mcp,
        verify=False,
    )

    res = await agent.run()

    assert res["status"] == "completed"
    assert res["steps"] == 3
    assert res["total_cost_inr"] == pytest.approx(0.0152, abs=1e-4)

    # Verify audit files exist and are well-formed
    trace_file = out_dir / "trace.jsonl"
    report_file = out_dir / "report.json"
    result_md = out_dir / "result.md"

    assert trace_file.exists()
    assert report_file.exists()
    assert result_md.exists()

    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["status"] == "completed"
    assert report_data["steps_executed"] == 3
    assert len(report_data["artifacts"]) == 1


@pytest.mark.asyncio
async def test_agent_budget_stops_before_expensive_call(tmp_path: Path):
    """Agent refuses to execute a tool if estimated cost exceeds remaining budget."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out"

    # LLM requests expensive TTS of 10,000 characters (Rs 6.00) with a budget of only Rs 0.05
    llm_script = [
        LLMResponse(
            content="Plan: Synthesize large text.",
            tool_calls=[
                ToolCallItem(
                    id="c1",
                    name="indic_speak",
                    arguments={
                        "input_text": "A" * 10000,
                        "voice": "Amit",
                        "language": "en",
                        "output_path": "out/artifacts/big.wav",
                    },
                )
            ],
        ),
    ]

    fake_llm = ScriptedFakeLLMClient(llm_script)
    fake_mcp = FakeMcpClient()

    agent = AgentLoop(
        task="Speak large text.",
        files_dir=files_dir,
        out_dir=out_dir,
        max_cost_inr=0.05,  # Tiny budget
        llm_client=fake_llm,
        mcp_client=fake_mcp,
    )

    res = await agent.run()

    assert res["status"] in ("budget_stopped", "budget_exceeded")
    assert len(fake_mcp.calls) == 0  # Zero tool calls executed!
    assert "BUDGET_EXCEEDED" in res["explanation"]


@pytest.mark.asyncio
async def test_agent_duplicate_call_returns_earlier_result(tmp_path: Path):
    """Exact duplicate tool call returns previous result with Rs 0.00 additional cost."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out"

    # LLM makes the identical translate call twice
    llm_script = [
        LLMResponse(
            content="Translating first time.",
            tool_calls=[
                ToolCallItem(
                    id="c1",
                    name="indic_translate",
                    arguments={"text": "Class starts at nine.", "target_language": "hi"},
                )
            ],
        ),
        LLMResponse(
            content="Translating exact same text again.",
            tool_calls=[
                ToolCallItem(
                    id="c2",
                    name="indic_translate",
                    arguments={"text": "Class starts at nine.", "target_language": "hi"},
                )
            ],
        ),
        LLMResponse(content="Done."),
    ]

    fake_llm = ScriptedFakeLLMClient(llm_script)
    fake_mcp = FakeMcpClient(
        {
            "indic_translate": lambda args: {
                "success": True,
                "translated_text": "क्लास नौ बजे शुरू होती है।",
                "estimated_cost_inr": 0.00018,
            }
        }
    )

    agent = AgentLoop(
        task="Test duplicates.",
        files_dir=files_dir,
        out_dir=out_dir,
        llm_client=fake_llm,
        mcp_client=fake_mcp,
        verify=False,
    )

    res = await agent.run()

    assert res["status"] == "completed"
    # The actual MCP client was invoked only ONCE!
    assert len(fake_mcp.calls) == 1
    # Cost was charged only once
    assert res["total_cost_inr"] == pytest.approx(0.00018, abs=1e-5)


@pytest.mark.asyncio
async def test_agent_indic_text_invented_by_llm_is_rejected(tmp_path: Path):
    """Tool call arguments containing raw Indian-script text authored by the LLM are rejected by the guard."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out"

    # LLM writes Hindi text directly in arguments
    llm_script = [
        LLMResponse(
            content="I will translate this Hindi sentence.",
            tool_calls=[
                ToolCallItem(
                    id="c1",
                    name="indic_translate",
                    arguments={"text": "नमस्ते दोस्तों", "target_language": "en"},
                )
            ],
        ),
        LLMResponse(content="I understand the policy restriction and will finish."),
    ]

    fake_llm = ScriptedFakeLLMClient(llm_script)
    fake_mcp = FakeMcpClient()

    agent = AgentLoop(
        task="Translate hindi text.",
        files_dir=files_dir,
        out_dir=out_dir,
        llm_client=fake_llm,
        mcp_client=fake_mcp,
    )

    await agent.run()

    # Tool call was rejected before reaching MCP client
    assert len(fake_mcp.calls) == 0
    assert any("POLICY VIOLATION" in miss for miss in agent.quality_target_misses)


@pytest.mark.asyncio
async def test_agent_missing_file_ends_as_impossible(tmp_path: Path):
    """When a required file is missing or language is unsupported, agent ends as impossible before paid calls."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out"

    fake_llm = ScriptedFakeLLMClient([])
    fake_mcp = FakeMcpClient()

    # Missing file mentioned in task
    agent = AgentLoop(
        task="Please transcribe missing_recording.wav and summarize it.",
        files_dir=files_dir,
        out_dir=out_dir,
        llm_client=fake_llm,
        mcp_client=fake_mcp,
    )

    res = await agent.run()
    assert res["status"] == "impossible"
    assert "missing_recording.wav" in res["explanation"]
    assert len(fake_mcp.calls) == 0

    # Unsupported language
    fake_llm2 = ScriptedFakeLLMClient(
        [
            LLMResponse(
                content="Plan: Task asks for French which is unsupported by Bodhan models.",
                tool_calls=[],
            ),
            LLMResponse(
                content="Task is impossible.",
                tool_calls=[
                    ToolCallItem(
                        id="fin",
                        name="finish",
                        arguments={
                            "status": "impossible",
                            "explanation": "Unsupported language 'french'. Bodhan AI models exclusively support 22 scheduled Indian languages and English.",
                        },
                    )
                ],
            ),
        ]
    )
    agent2 = AgentLoop(
        task="Translate this document to French.",
        files_dir=files_dir,
        out_dir=out_dir,
        llm_client=fake_llm2,
        mcp_client=fake_mcp,
    )
    res2 = await agent2.run()
    assert res2["status"] == "impossible"
    assert "Unsupported language 'french'" in res2["explanation"]


@pytest.mark.asyncio
async def test_agent_max_steps_stops(tmp_path: Path):
    """Execution halts when max_steps ceiling is reached."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    out_dir = tmp_path / "out"

    # Infinite loop of tool calls
    infinite_calls = [
        LLMResponse(
            content=f"Loop step {i}",
            tool_calls=[
                ToolCallItem(id=f"c{i}", name="inspect_media", arguments={"path": f"f{i}.txt"})
            ],
        )
        for i in range(10)
    ]

    fake_llm = ScriptedFakeLLMClient(infinite_calls)
    fake_mcp = FakeMcpClient(
        {"inspect_media": lambda args: {"success": True, "estimated_cost_inr": 0.0}}
    )

    agent = AgentLoop(
        task="Loop forever.",
        files_dir=files_dir,
        out_dir=out_dir,
        max_steps=3,  # Cap at 3 steps
        llm_client=fake_llm,
        mcp_client=fake_mcp,
    )

    res = await agent.run()
    assert res["status"] == "max_steps"


@pytest.mark.asyncio
async def test_agent_end_to_end_real_mcp_server(tmp_path: Path):
    """End-to-end test starting real 'python -m bodhan_mcp' over stdio and calling local tools."""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    sample_file = files_dir / "sample.txt"
    sample_file.write_text("Hello from real MCP stdio integration.", encoding="utf-8")

    out_dir = tmp_path / "out"

    # LLM calls real read_text_file and inspect_media on the real MCP server
    llm_script = [
        LLMResponse(
            content="Plan: Inspect sample.txt and read its content.",
            tool_calls=[
                ToolCallItem(id="c1", name="read_text_file", arguments={"path": "sample.txt"}),
            ],
        ),
        LLMResponse(content="Finished reading file."),
    ]

    fake_llm = ScriptedFakeLLMClient(llm_script)

    # Real MCP Client running over stdio!
    real_mcp = BodhanMcpClient(files_dir=str(files_dir))
    await real_mcp.start()

    try:
        agent = AgentLoop(
            task="Inspect and read sample.txt.",
            files_dir=files_dir,
            out_dir=out_dir,
            llm_client=fake_llm,
            mcp_client=real_mcp,
        )

        res = await agent.run()
        assert res["status"] == "completed"
        assert res["steps"] == 1

        # Check trace
        trace_lines = (out_dir / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(trace_lines) >= 2  # plan + tool
        assert any(json.loads(line).get("name") == "read_text_file" for line in trace_lines)

    finally:
        await real_mcp.close()
