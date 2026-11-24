"""Fetch real speech audio from Hugging Face and convert into multiple standard audio formats.

Formats generated:
  - speech_16k.wav        (16kHz mono PCM WAV)
  - speech_compressed.mp3 (MP3 128kbps)
  - speech_aac.m4a        (AAC M4A container)
  - speech_lossless.flac  (FLAC lossless)
  - speech_vorbis.ogg     (OGG Vorbis)
  - speech_stereo.wav     (Stereo WAV)
  - speech_long_45s.wav   (45s multi-chunk speech with pause boundaries)
"""

import subprocess
import sys
from pathlib import Path

import httpx


HF_AUDIO_URL = "https://huggingface.co/datasets/Narsil/asr_dummy/resolve/main/1.flac"


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg command and verify success."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {res.stderr}")


def prepare_audio_formats(out_dir: Path | str = "demo/inputs/audio_formats") -> dict[str, Path]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download base speech clip from Hugging Face
    raw_flac = output_dir / "base_hf_speech.flac"
    if not raw_flac.exists():
        print(f"Downloading base speech audio from Hugging Face ({HF_AUDIO_URL})...")
        r = httpx.get(HF_AUDIO_URL, follow_redirects=True, timeout=30.0)
        r.raise_for_status()
        raw_flac.write_bytes(r.content)
        print(f"Downloaded {len(r.content)} bytes.")

    format_files = {}

    # 2. Standard 16kHz mono WAV
    wav_path = output_dir / "speech_16k.wav"
    run_ffmpeg(["-i", str(raw_flac), "-ac", "1", "-ar", "16000", str(wav_path)])
    format_files["wav"] = wav_path

    # 3. MP3 (128 kbps)
    mp3_path = output_dir / "speech_compressed.mp3"
    run_ffmpeg(["-i", str(raw_flac), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3_path)])
    format_files["mp3"] = mp3_path

    # 4. M4A / AAC
    m4a_path = output_dir / "speech_aac.m4a"
    run_ffmpeg(["-i", str(raw_flac), "-c:a", "aac", "-b:a", "128k", str(m4a_path)])
    format_files["m4a"] = m4a_path

    # 5. FLAC
    flac_path = output_dir / "speech_lossless.flac"
    run_ffmpeg(["-i", str(raw_flac), "-c:a", "flac", str(flac_path)])
    format_files["flac"] = flac_path

    # 6. OGG Vorbis
    ogg_path = output_dir / "speech_vorbis.ogg"
    run_ffmpeg(["-i", str(raw_flac), "-c:a", "libvorbis", "-q:a", "4", str(ogg_path)])
    format_files["ogg"] = ogg_path

    # 7. Stereo WAV
    stereo_path = output_dir / "speech_stereo.wav"
    run_ffmpeg(["-i", str(raw_flac), "-ac", "2", "-ar", "44100", str(stereo_path)])
    format_files["stereo_wav"] = stereo_path

    # 8. Long 45-second audio clip with silence pauses (>30s limit to test chunking)
    long_path = output_dir / "speech_long_45s.wav"
    # Concatenate repeating clip with 500ms pause gaps
    filter_complex = (
        "[0:a]adelay=0|0[a0];"
        "[0:a]adelay=10000|10000[a1];"
        "[0:a]adelay=20000|20000[a2];"
        "[0:a]adelay=30000|30000[a3];"
        "[0:a]adelay=40000|40000[a4];"
        "[a0][a1][a2][a3][a4]amix=inputs=5:dropout_transition=0:normalize=0[out]"
    )
    run_ffmpeg(["-i", str(wav_path), "-filter_complex", filter_complex, "-map", "[out]", "-t", "45", str(long_path)])
    format_files["long_45s"] = long_path

    print(f"\nGenerated {len(format_files)} audio test formats in {output_dir}:")
    for fmt, p in format_files.items():
        size_kb = round(p.stat().st_size / 1024, 1)
        print(f"  • {fmt:<12}: {p.name:<24} ({size_kb} KB)")

    return format_files


if __name__ == "__main__":
    prepare_audio_formats()
