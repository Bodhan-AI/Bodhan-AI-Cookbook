
import tempfile
import wave
from pathlib import Path

from bodhan_agent.audio import AudioChunker


def create_test_wav(path, duration_seconds=5):
    sample_rate = 8000
    total_frames = sample_rate * duration_seconds

    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)

        audio.writeframes(
            b"\x00\x00" * total_frames
        )


def main():
    print("Testing OmniAgent AI Audio Chunking...\n")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)

        source = root / "sample.wav"
        output = root / "chunks"

        create_test_wav(
            source,
            duration_seconds=5,
        )

        chunker = AudioChunker(
            chunk_seconds=2
        )

        chunks = chunker.split_wav(
            source,
            output,
        )

        assert len(chunks) == 3

        print("PASS: WAV audio split into 3 chunks!")

        expected_durations = [2.0, 2.0, 1.0]

        for chunk, expected in zip(
            chunks, expected_durations
        ):
            assert chunk["duration_seconds"] == expected
            assert Path(chunk["path"]).exists()

        print("PASS: Chunk durations verified!")

        total_frames = 0

        for chunk in chunks:
            with wave.open(
                chunk["path"], "rb"
            ) as audio:
                assert audio.getframerate() == 8000
                assert audio.getnchannels() == 1
                assert audio.getsampwidth() == 2

                total_frames += audio.getnframes()

        assert total_frames == 5 * 8000

        print("PASS: Audio format preserved!")
        print("PASS: No audio frames lost!")

        try:
            AudioChunker(chunk_seconds=0)
        except ValueError:
            print("PASS: Invalid chunk size rejected!")
        else:
            raise AssertionError(
                "Invalid chunk size was accepted."
            )

    print("\nPASS: Audio chunking module works!")


if __name__ == "__main__":
    main()
