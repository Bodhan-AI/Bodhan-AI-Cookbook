
import wave
from pathlib import Path


class AudioChunker:
    """Split uncompressed WAV audio into timed chunks."""

    def __init__(self, chunk_seconds=30):
        if chunk_seconds <= 0:
            raise ValueError(
                "chunk_seconds must be positive."
            )

        self.chunk_seconds = chunk_seconds

    def split_wav(self, input_path, output_dir):
        input_path = Path(input_path)
        output_dir = Path(output_dir)

        if not input_path.is_file():
            raise FileNotFoundError(
                f"Audio file not found: {input_path}"
            )

        if input_path.suffix.lower() != ".wav":
            raise ValueError(
                "This chunker supports WAV files only."
            )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        chunks = []

        with wave.open(str(input_path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_rate = source.getframerate()
            total_frames = source.getnframes()

            if frame_rate <= 0:
                raise ValueError(
                    "Invalid WAV sample rate."
                )

            frames_per_chunk = max(
                1,
                int(frame_rate * self.chunk_seconds),
            )

            index = 0
            processed_frames = 0

            while processed_frames < total_frames:
                frame_count = min(
                    frames_per_chunk,
                    total_frames - processed_frames,
                )

                audio_data = source.readframes(
                    frame_count
                )

                index += 1

                chunk_path = (
                    output_dir
                    / f"{input_path.stem}_chunk_{index:03d}.wav"
                )

                with wave.open(
                    str(chunk_path), "wb"
                ) as target:
                    target.setnchannels(channels)
                    target.setsampwidth(sample_width)
                    target.setframerate(frame_rate)
                    target.writeframes(audio_data)

                start_seconds = (
                    processed_frames / frame_rate
                )

                processed_frames += frame_count

                end_seconds = (
                    processed_frames / frame_rate
                )

                chunks.append({
                    "index": index,
                    "path": str(chunk_path.resolve()),
                    "start_seconds": round(
                        start_seconds, 3
                    ),
                    "end_seconds": round(
                        end_seconds, 3
                    ),
                    "duration_seconds": round(
                        end_seconds - start_seconds, 3
                    ),
                })

        return chunks
