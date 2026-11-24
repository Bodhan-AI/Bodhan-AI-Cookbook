"""Generate the benchmark's Hindi, Telugu, Tamil, and Bengali WAV fixtures on macOS.

Uses the built-in `say` voices and FFmpeg; no network or Bodhan API calls.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "media" / "audio"
FFMPEG = "/opt/homebrew/bin/ffmpeg"


def synthesize(text: str, voice: str, output: Path, directory: Path) -> None:
    aiff = directory / f"{output.stem}.aiff"
    subprocess.run(["say", "-v", voice, "-r", "155", "-o", str(aiff), text], check=True)
    subprocess.run([
        FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(aiff),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
    ], check=True)


def wav_frames(path: Path) -> tuple[wave._wave_params, bytes]:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if params.sampwidth != 2 or params.nchannels != 1 or params.framerate != 16_000:
            raise ValueError(f"Unexpected audio format in {path}: {params}")
        return params, source.readframes(source.getnframes())


def main() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    batch = AUDIO / "clip_batch_01"
    batch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bodhan-eval-audio-") as temp:
        work = Path(temp)
        synthesize("विद्यालय आज दोपहर तीन बजे बंद होगा।", "Lekha", AUDIO / "clip_ambiguous_01.wav", work)
        synthesize("कल विद्यालय सुबह नौ बजे खुलेगा।", "Lekha", AUDIO / "hindi_notice_01.wav", work)
        synthesize("नमस्ते, आज हम गणित की कक्षा शुरू करेंगे। कृपया अपनी पुस्तकें खोलिए।", "Lekha", AUDIO / "short_hi.wav", work)

        # Five distinct language-labelled clips for the language-selection task.
        synthesize("విద్యార్థులారా, రేపు పాఠశాల ఉదయం తొమ్మిది గంటలకు ప్రారంభమవుతుంది.", "Geeta", batch / "telugu_01.wav", work)
        synthesize("विद्यालय आज दोपहर तीन बजे बंद होगा।", "Lekha", batch / "hindi_01.wav", work)
        synthesize("நாளை காலை ஒன்பது மணிக்கு பள்ளி திறக்கப்படும்.", "Vani", batch / "tamil_01.wav", work)
        synthesize("దయచేసి రేపటి పాఠం కోసం మీ పుస్తకాలు తీసుకురండి.", "Geeta", batch / "telugu_02.wav", work)
        synthesize("আগামীকাল সকাল নয়টায় বিদ্যালয় খুলবে।", "Piya", batch / "bengali_01.wav", work)

        segments = [
            "विद्यार्थियों, आज हम विद्यालय की छत पर बने बगीचे के बारे में बात करेंगे। हर कक्षा पौधों की ऊँचाई देखे और तारीख के साथ माप अपनी कॉपी में लिखे। पौधों को जरूरत के अनुसार ही पानी दें।",
            "पौधों को धूप और साफ पानी चाहिए। पत्तों पर दाग दिखें तो शिक्षक को बताइए। फूल मधुमक्खियों और तितलियों को आकर्षित करते हैं, इसलिए उन्हें दूर से देखें। बगीचे में किसी भी कीट को हाथ न लगाएँ।",
            "सप्ताह के अंत में हर समूह अपने निरीक्षण कक्षा में बताएगा। पौधों की ऊँचाई, पत्तों की संख्या और मिट्टी की नमी की तुलना करें। अगले सप्ताह नए बीज बोकर परिणाम फिर से लिखेंगे।",
        ]
        pcm_segments: list[bytes] = []
        params = None
        for index, text in enumerate(segments, start=1):
            wav = work / f"long_segment_{index}.wav"
            synthesize(text, "Lekha", wav, work)
            current, raw = wav_frames(wav)
            params = current
            slot_ms = 25_000 if index < 3 else 17_000
            slot_frames = slot_ms * current.framerate // 1000
            if len(raw) // current.nchannels // current.sampwidth > slot_frames:
                raise RuntimeError(f"Long-audio segment {index} exceeds its {slot_ms / 1000:.0f}-second slot; shorten the fixture text")
            silence = b"\0\0" * (slot_frames - len(raw) // 2)
            pcm_segments.append(raw + silence)
        assert params is not None
        long_path = AUDIO / "long_hi_67s.wav"
        with wave.open(str(long_path), "wb") as target:
            target.setparams(params)
            target.writeframes(b"".join(pcm_segments))
    print(f"Created WAV fixtures under {AUDIO}")


if __name__ == "__main__":
    main()
