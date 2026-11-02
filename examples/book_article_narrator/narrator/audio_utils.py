import io
import wave


def join_wavs(clips, pause_ms=300):
    """Join WAV clips (bytes) into one WAV (bytes), with silence between them.

    pause_ms is either one number (the same pause everywhere) or a list with one
    pause for each gap between clips.
    """
    if not clips:
        raise ValueError("No audio clips to join.")

    gaps = len(clips) - 1
    if isinstance(pause_ms, int):
        pauses = [pause_ms] * gaps
    else:
        pauses = list(pause_ms)
        if len(pauses) != gaps:
            raise ValueError("Give one pause for each gap between clips.")

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        first_format = None
        for index, clip in enumerate(clips):
            with wave.open(io.BytesIO(clip), "rb") as reader:
                audio_format = (
                    reader.getnchannels(),
                    reader.getsampwidth(),
                    reader.getframerate(),
                )
                if first_format is None:
                    first_format = audio_format
                    writer.setnchannels(audio_format[0])
                    writer.setsampwidth(audio_format[1])
                    writer.setframerate(audio_format[2])
                elif audio_format != first_format:
                    raise ValueError("All clips must have the same audio format.")
                if index > 0:
                    writer.writeframes(_silence(first_format, pauses[index - 1]))
                writer.writeframes(reader.readframes(reader.getnframes()))
    return out.getvalue()


def _silence(audio_format, pause_ms):
    """Zero-valued samples that last `pause_ms` milliseconds."""
    channels, sample_width, frame_rate = audio_format
    frames = int(frame_rate * pause_ms / 1000)
    return b"\x00" * (frames * channels * sample_width)