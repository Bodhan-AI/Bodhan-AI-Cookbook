
import inspect
from pathlib import Path

from .audio import AudioChunker


class ChunkedTranscriber:
    """Transcribe WAV chunks sequentially and merge results."""

    def __init__(self, chunk_seconds=30):
        self.chunker = AudioChunker(
            chunk_seconds=chunk_seconds
        )

    async def transcribe(
        self,
        audio_path,
        language,
        output_dir,
        transcribe_chunk,
    ):
        """
        Split WAV audio and transcribe each chunk.

        transcribe_chunk must accept:
            audio_path
            language

        Expected Bodhan response:
            {
                "status": "success",
                "transcription": "..."
            }
        """

        if not callable(transcribe_chunk):
            raise ValueError(
                "transcribe_chunk must be callable."
            )

        output_dir = Path(output_dir)

        chunks = self.chunker.split_wav(
            input_path=audio_path,
            output_dir=output_dir,
        )

        if not chunks:
            raise ValueError(
                "Audio file contains no frames."
            )

        results = []
        transcripts = []

        for chunk in chunks:
            response = transcribe_chunk(
                audio_path=chunk["path"],
                language=language,
            )

            if inspect.isawaitable(response):
                response = await response

            if not isinstance(response, dict):
                raise RuntimeError(
                    "Transcription returned an invalid response."
                )

            if response.get("status") != "success":
                raise RuntimeError(
                    f"Transcription failed for chunk "
                    f"{chunk['index']}: "
                    f"{response.get('message', 'Unknown error')}"
                )

            text = response.get("transcription")

            if not isinstance(text, str):
                raise RuntimeError(
                    f"Chunk {chunk['index']} "
                    "did not return transcription."
                )

            results.append({
                "index": chunk["index"],
                "start_seconds": chunk["start_seconds"],
                "end_seconds": chunk["end_seconds"],
                "text": text,
            })

            if text.strip():
                transcripts.append(text.strip())

        return {
            "status": "success",
            "source_file": str(
                Path(audio_path).resolve()
            ),
            "language": language,
            "total_chunks": len(chunks),
            "transcription": " ".join(transcripts),
            "chunks": results,
        }
