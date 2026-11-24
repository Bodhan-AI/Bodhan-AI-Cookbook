
import asyncio
import tempfile
import wave
from pathlib import Path

from bodhan_agent.transcription_mcp import MCPChunkedTranscription


def create_test_wav(path, seconds=5):
    sample_rate = 8000

    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(
            b"\x00\x00" * sample_rate * seconds
        )


class FakeAgent:
    """Simulate the OmniAgent MCP execution method."""

    def __init__(self):
        self.calls = []

    async def execute_with_retry(
        self,
        session,
        plan,
        budget,
        step,
        max_attempts,
    ):
        budget.reserve_tool_call()

        self.calls.append(plan)

        return {
            "status": "success",
            "transcription": f"chunk {len(self.calls)}",
        }


class FakeBudget:
    """Simulate the real ExecutionBudget interface."""

    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    @property
    def remaining(self):
        return self.limit - self.used

    def reserve_tool_call(self):
        if self.used >= self.limit:
            raise RuntimeError(
                "Tool-call budget exceeded."
            )

        self.used += 1


async def main():
    print(
        "Testing MCP Chunked Transcription Adapter...\n"
    )

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        audio_path = root / "sample.wav"

        create_test_wav(audio_path, seconds=5)

        # TEST 1: Successful chunked transcription
        agent = FakeAgent()
        budget = FakeBudget(limit=3)

        adapter = MCPChunkedTranscription(
            agent=agent,
            chunk_seconds=2,
        )

        result = await adapter.transcribe(
            session=None,
            audio_path=audio_path,
            language="hi",
            output_dir=root / "chunks",
            budget=budget,
            step=1,
        )

        assert result["status"] == "success"
        assert result["total_chunks"] == 3

        print("PASS: Three chunks processed!")

        # TEST 2: Transcript merging
        assert result["transcription"] == (
            "chunk 1 chunk 2 chunk 3"
        )

        print("PASS: Chunk results merged!")

        # TEST 3: Budget consumption
        assert budget.used == 3
        assert budget.remaining == 0
        assert len(agent.calls) == 3

        print(
            "PASS: Every chunk consumes one tool call!"
        )

        # TEST 4: Correct MCP tool
        assert all(
            call["tool"] == "transcribe_audio"
            for call in agent.calls
        )

        print(
            "PASS: Correct Bodhan MCP tool selected!"
        )

        # TEST 5: Language preservation
        assert all(
            call["arguments"]["language"] == "hi"
            for call in agent.calls
        )

        print("PASS: Language preserved!")

        # TEST 6: Correct chunk paths
        assert all(
            Path(call["arguments"]["audio_path"]).is_file()
            for call in agent.calls
        )

        print("PASS: Valid WAV chunk paths passed!")

        # TEST 7: Pre-check insufficient budget
        limited_agent = FakeAgent()
        limited_budget = FakeBudget(limit=2)

        limited_adapter = MCPChunkedTranscription(
            agent=limited_agent,
            chunk_seconds=2,
        )

        try:
            await limited_adapter.transcribe(
                session=None,
                audio_path=audio_path,
                language="hi",
                output_dir=root / "limited_chunks",
                budget=limited_budget,
                step=1,
            )

        except ValueError as exc:
            assert (
                "Insufficient tool-call budget"
                in str(exc)
            )

            print(
                "PASS: Insufficient budget detected!"
            )

        else:
            raise AssertionError(
                "Insufficient budget was accepted."
            )

        # TEST 8: No API calls on insufficient budget
        assert len(limited_agent.calls) == 0
        assert limited_budget.used == 0

        print(
            "PASS: Zero calls made when budget is insufficient!"
        )

        # TEST 9: Required calls calculation
        assert adapter.required_calls(audio_path) == 3

        print(
            "PASS: Required chunk count calculated correctly!"
        )

    print(
        "\nPASS: All MCP chunk adapter tests passed!"
    )


if __name__ == "__main__":
    asyncio.run(main())
