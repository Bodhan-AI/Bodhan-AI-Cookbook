import asyncio
import tempfile
import wave
from pathlib import Path
from types import MethodType

from bodhan_agent.agent import OmniAgent


class FakeBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    @property
    def remaining(self):
        return self.limit - self.used

    def reserve_tool_call(self):
        if self.remaining <= 0:
            raise RuntimeError("Tool-call budget exceeded")
        self.used += 1


def make_wav(path, seconds=65):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(b"\x00\x00" * 8000 * seconds)


def fake_agent():
    # Bypass __init__: no environment variables, Gemini or MCP connection needed.
    agent = OmniAgent.__new__(OmniAgent)
    calls = []

    async def fake_execute(self, session, plan, budget, step, max_attempts):
        budget.reserve_tool_call()
        calls.append(plan)
        if plan["tool"] == "transcribe_audio":
            return {"status": "success", "transcription": f"part {len(calls)}"}
        return {"status": "success", "mock": True}

    agent.execute_with_retry = MethodType(fake_execute, agent)
    return agent, calls


async def main():
    print("Testing OmniAgent chunk routing (offline)...\n")
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "sample.wav"
        make_wav(wav)
        plan = {"tool": "transcribe_audio", "arguments": {"audio_path": str(wav), "language": "hi"}}

        agent, calls = fake_agent()
        budget = FakeBudget(3)
        result = await agent.execute_planned_action(None, plan, budget, 1)
        assert result["total_chunks"] == 3
        assert result["transcription"] == "part 1 part 2 part 3"
        assert budget.used == 3 and len(calls) == 3
        print("PASS: WAV routed through three budgeted MCP calls")

        chunk_paths = [Path(c["arguments"]["audio_path"]) for c in calls]
        assert all(not p.exists() for p in chunk_paths)
        print("PASS: Temporary chunks cleaned up")

        agent2, calls2 = fake_agent()
        try:
            await agent2.execute_planned_action(None, plan, FakeBudget(2), 1)
        except ValueError as exc:
            assert "Insufficient tool-call budget" in str(exc)
        else:
            raise AssertionError("Insufficient budget was accepted")
        assert not calls2
        print("PASS: Insufficient budget blocks all MCP calls")

        for name in ("sample.mp3", "sample.m4a", "sample.flac"):
            agent3, calls3 = fake_agent()
            p = {"tool": "transcribe_audio", "arguments": {"audio_path": str(Path(d) / name), "language": "hi"}}
            await agent3.execute_planned_action(None, p, FakeBudget(1), 1)
            assert len(calls3) == 1 and calls3[0] is p
        print("PASS: Non-WAV transcription keeps original MCP path")

        agent4, calls4 = fake_agent()
        other = {"tool": "translate_text", "arguments": {"text": "hello", "source_lang": "en", "target_lang": "hi"}}
        await agent4.execute_planned_action(None, other, FakeBudget(1), 1)
        assert len(calls4) == 1 and calls4[0] is other
        print("PASS: Other MCP tools keep original execution path")

    print("\nPASS: All OmniAgent chunk routing tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
