
import asyncio
import tempfile
import wave
from pathlib import Path

from bodhan_agent.transcription import ChunkedTranscriber


def create_test_wav(path, seconds=5):
    sample_rate = 8000

    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(
            b"\x00\x00" * sample_rate * seconds
        )


async def main():
    print(
        "Testing Bodhan-Compatible Chunked Transcription...\n"
    )

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        audio_path = root / "sample.wav"
        chunks_dir = root / "chunks"

        create_test_wav(audio_path, seconds=5)

        calls = []

        async def fake_bodhan_transcribe(
            audio_path,
            language,
        ):
            calls.append(audio_path)

            texts = [
                "नमस्ते",
                "मेरा नाम",
                "समीर है",
            ]

            return {
                "status": "success",
                "transcription": texts[len(calls) - 1],
                "model": "indic-transcribe",
            }

        transcriber = ChunkedTranscriber(
            chunk_seconds=2
        )

        result = await transcriber.transcribe(
            audio_path=audio_path,
            language="hi",
            output_dir=chunks_dir,
            transcribe_chunk=fake_bodhan_transcribe,
        )

        assert result["status"] == "success"
        assert result["total_chunks"] == 3
        assert len(calls) == 3

        print("PASS: Three chunks processed!")

        assert result["transcription"] == (
            "नमस्ते मेरा नाम समीर है"
        )

        print("PASS: Bodhan transcription format supported!")

        assert [
            chunk["index"]
            for chunk in result["chunks"]
        ] == [1, 2, 3]

        print("PASS: Chunk ordering preserved!")

        async def failing_bodhan_transcribe(
            audio_path,
            language,
        ):
            return {
                "status": "error",
                "message": "Simulated API failure",
            }

        try:
            await transcriber.transcribe(
                audio_path=audio_path,
                language="hi",
                output_dir=chunks_dir,
                transcribe_chunk=failing_bodhan_transcribe,
            )
        except RuntimeError:
            print("PASS: Bodhan API failures detected!")
        else:
            raise AssertionError(
                "Failed transcription was accepted."
            )

    print(
        "\nPASS: Bodhan-compatible chunked transcription!"
    )


if __name__ == "__main__":
    asyncio.run(main())
