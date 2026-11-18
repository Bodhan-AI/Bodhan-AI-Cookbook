
import wave
from pathlib import Path

from .transcription import ChunkedTranscriber


class MCPChunkedTranscription:
    """Connect WAV chunking to OmniAgent's MCP execution."""

    def __init__(self, agent, chunk_seconds=30):
        self.agent = agent
        self.transcriber = ChunkedTranscriber(
            chunk_seconds=chunk_seconds
        )

    def required_calls(self, audio_path):
        """Calculate the minimum number of WAV chunks."""

        path = Path(audio_path)

        with wave.open(str(path), "rb") as audio:
            frame_count = audio.getnframes()
            sample_rate = audio.getframerate()

        if sample_rate <= 0 or frame_count <= 0:
            raise ValueError(
                "Audio must contain valid audio frames."
            )

        frames_per_chunk = max(
            1,
            int(sample_rate * self.transcriber.chunker.chunk_seconds),
        )

        return (
            frame_count + frames_per_chunk - 1
        ) // frames_per_chunk

    async def transcribe(
        self,
        session,
        audio_path,
        language,
        output_dir,
        budget,
        step,
        max_attempts=3,
    ):
        required = self.required_calls(audio_path)

        if budget.remaining < required:
            raise ValueError(
                f"Insufficient tool-call budget: "
                f"{required} calls required, "
                f"{budget.remaining} remaining."
            )

        async def transcribe_chunk(audio_path, language):
            plan = {
                "action": "call_tool",
                "tool": "transcribe_audio",
                "arguments": {
                    "audio_path": str(Path(audio_path).resolve()),
                    "language": language,
                },
                "reason": "Transcribe one WAV audio chunk.",
            }

            return await self.agent.execute_with_retry(
                session=session,
                plan=plan,
                budget=budget,
                step=step,
                max_attempts=max_attempts,
            )

        return await self.transcriber.transcribe(
            audio_path=audio_path,
            language=language,
            output_dir=output_dir,
            transcribe_chunk=transcribe_chunk,
        )
