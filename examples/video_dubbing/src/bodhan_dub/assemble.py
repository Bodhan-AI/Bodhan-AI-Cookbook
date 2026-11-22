"""Build the final dubbed audio track and mux it into the video.

Audio = dubbed speech clips (normalized to -16 LUFS) + the ORIGINAL audio everywhere except where the
original speaker talks (muted there, with short fades). So music, effects and silence stay as they were.
The audio length is forced to equal the video length.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import extract_audio, probe_media
from .subtitles import add_label_cue, clip_cues, write_srt

log = logging.getLogger(__name__)

SPEECH_SR = 24_000     # TTS clips
MIX_SR = 44_100
TARGET_LUFS = -16.0
TRUE_PEAK = -1.5
MUTE_MARGIN = 0.05     # seconds of extra muting around original speech
FADE = 0.04            # seconds, fade in/out of the original around muted parts
# Video codecs an MP4 can carry as they are, so the picture is copied untouched (fast, no quality loss).
# Anything else (measured 2026-11-22: ProRes from MOV and VP8 from WebM make the MP4 mux fail) is re-encoded
# to H.264 once per language. MPEG-2 and similar would mux, but most players cannot play them from an MP4.
MP4_COPY_CODECS = {"h264", "hevc", "av1", "vp9", "mpeg4"}
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]   # yuv420p needs even width and height


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr.strip()[-500:]}")
    return r


def video_duration(path: str | Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(r.stdout.strip())


def stream_durations(path: str | Path) -> dict[str, float]:
    r = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)])
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(r.stdout)["streams"] if "duration" in s}


# --------------------------------------------------------------------------- pure helpers
def build_speech_track(recs: list[dict], total_dur: float, sr: int = SPEECH_SR) -> np.ndarray:
    """Mono track of exactly total_dur seconds with each clip placed at its placed_start."""
    track = np.zeros(int(round(total_dur * sr)), dtype="float32")
    for r in recs:
        if "clip_path" not in r:
            continue
        w, csr = sf.read(r["clip_path"], dtype="float32")
        w = w if w.ndim == 1 else w.mean(axis=1)
        if csr != sr:
            raise ValueError(f"clip {r['clip_path']} is {csr} Hz, expected {sr}")
        i = int(round(r["placed_start"] * sr))
        if i < 0:  # silent lead before the start of the file
            w, i = w[-i:], 0
        j = min(i + len(w), len(track))
        if i < len(track):
            track[i:j] += w[: j - i]
    return track


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def mute_envelope(n: int, sr: int, intervals: list[tuple[float, float]], fade: float = FADE) -> np.ndarray:
    """1.0 everywhere, 0.0 inside intervals, with ~`fade`-second linear ramps at the edges: a `fade`-long moving
    average of the 0/1 mask. It is computed only near the intervals (elsewhere it is exactly 1.0), so memory stays
    one float32 track. Measured 2026-11-22: the whole-track version peaked the mix at 3.9 GB on a 12.8-min video."""
    k = max(1, int(fade * sr))
    env = np.ones(n, dtype="float32")
    spans = [(max(0, int(a * sr)), min(n, int(b * sr))) for a, b in merge_intervals(intervals)]
    groups: list[list] = []          # intervals whose averaging windows touch are computed together
    for i, j in spans:
        if j <= i:
            continue
        lo, hi = max(0, i - k), min(n, j + k)
        if groups and lo <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], hi)
            groups[-1][2].append((i, j))
        else:
            groups.append([lo, hi, [(i, j)]])
    for lo, hi, ivs in groups:
        a0, b0 = max(0, lo - k // 2), min(n, hi + k - k // 2)
        mask = np.ones(b0 - a0, dtype="float64")
        for i, j in ivs:
            mask[i - a0 : j - a0] = 0.0
        cs = np.concatenate([[0.0], np.cumsum(mask)])
        t = np.arange(lo, hi)
        wl = np.clip(t - k // 2, 0, n) - a0
        wh = np.clip(t + k - k // 2, 0, n) - a0
        env[lo:hi] = (cs[wh] - cs[wl]) / np.maximum(wh - wl, 1)
    return env


def first_voiced_time(path: str | Path, rel: float = 0.02) -> float:
    """Seconds of leading silence in a clip (first sample above rel * peak)."""
    w, sr = sf.read(str(path), dtype="float32")
    w = w if w.ndim == 1 else w.mean(axis=1)
    peak = float(np.abs(w).max()) if len(w) else 0.0
    idx = np.where(np.abs(w) > rel * peak)[0] if peak > 0 else []
    return float(idx[0] / sr) if len(idx) else 0.0


# --------------------------------------------------------------------------- loudness
def measure_lufs(wav: str | Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", r.stderr)
    if not m:
        raise RuntimeError("could not measure loudness")
    return float(m[-1])


def normalize_loudness(src: str | Path, dst: str | Path, target: float = TARGET_LUFS, tp: float = TRUE_PEAK,
                       sr: int = SPEECH_SR, channels: int = 1) -> Path:
    """Two-pass linear loudnorm so the whole track hits the target without dynamic pumping."""
    p1 = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
                         f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True)
    blob = re.findall(r"\{[^{}]*\}", p1.stderr)
    if not blob:
        raise RuntimeError("loudnorm pass 1 produced no stats")
    s = json.loads(blob[-1])
    af = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={s['input_i']}:measured_TP={s['input_tp']}:"
          f"measured_LRA={s['input_lra']}:measured_thresh={s['input_thresh']}:offset={s['target_offset']}:linear=true")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", af,
         "-ar", str(sr), "-ac", str(channels), "-c:a", "pcm_s16le", str(dst)])
    return Path(dst)


# --------------------------------------------------------------------------- assemble
def assemble(recs: list[dict], segments: list[dict], video: str | Path, out_mp4: str | Path,
             work_dir: str | Path, duration: float | None = None, cues_key: str = "text",
             reencode: bool | None = None, mute_undubbed: bool = False,
             background: str | Path | None = None, sub_lang: str = "hin", label: str = "") -> dict:
    """Mix, normalize, mux. `duration` trims the output (e.g. a partial preview); default = whole video.

    recs: dub records with clip_path/placed_start/placed_end (from dub_language).
    segments: source segments (id, start, end) used to know where the original speaker talks.
    background: separated background (44.1 kHz stereo, container timeline). If given, it plays under the
                dubbed voice where the original is muted, so music and effects continue; else silence there.
    label: AI-dubbed notice (e.g. "AI-dubbed (Hindi)"): written as the MP4 title (players such as VLC show it
           on screen when playback starts), the comment, the audio track name and the first subtitle cue.
    Returns a report dict with durations, loudness and sync numbers.
    """
    work_dir, out_mp4, video = Path(work_dir), Path(out_mp4), Path(video)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    media = probe_media(video)
    vdur, origin = media["duration"], media["origin"]
    total = min(duration, vdur) if duration else vdur
    seg_by_id = {s["id"]: s for s in segments}
    placed = [r for r in recs if "clip_path" in r]

    # 1. dubbed speech track (24 kHz mono), as placed
    speech = build_speech_track(placed, total)
    raw_speech = work_dir / "speech_raw.wav"
    sf.write(str(raw_speech), speech, SPEECH_SR, subtype="PCM_16")
    lufs_before = measure_lufs(raw_speech)

    # 2. original audio (stereo) and the places where the original speaker talks
    bg_path = work_dir / "original_audio.wav"
    extract_audio(video, bg_path, sample_rate=MIX_SR, channels=2, duration=total)  # container-aligned
    bg, _ = sf.read(str(bg_path), dtype="float32")
    n = int(round(total * MIX_SR))
    bg = np.pad(bg, ((0, max(0, n - len(bg))), (0, 0)))[:n]
    # Original speech is muted under every dubbed segment. A segment that could not be dubbed keeps its
    # original audio (graceful fallback) unless mute_undubbed is set (used for partial previews).
    muted_segs = segments if mute_undubbed else [seg_by_id[r["id"]] for r in placed]
    mute = [(s["start"] - MUTE_MARGIN, s["end"] + MUTE_MARGIN) for s in muted_segs if s["start"] < total]
    env = mute_envelope(n, MIX_SR, mute)

    # 3. speech -> 44.1 kHz dual-mono stereo, THEN normalize: BS.1770 sums channel power, so normalizing
    #    the stereo version makes the delivered audio (not the mono file) measure the target loudness.
    sp44 = work_dir / "speech_44k.wav"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_speech), "-ar", str(MIX_SR), "-ac", "1",
         "-c:a", "pcm_s16le", str(sp44)])
    sp_mono, _ = sf.read(str(sp44), dtype="float32")
    sp_mono = np.pad(sp_mono, (0, max(0, n - len(sp_mono))))[:n]
    stereo_raw = work_dir / "speech_44k_stereo.wav"
    sf.write(str(stereo_raw), np.column_stack([sp_mono, sp_mono]), MIX_SR, subtype="PCM_16")
    norm_speech = normalize_loudness(stereo_raw, work_dir / "speech_norm.wav", sr=MIX_SR, channels=2)
    lufs_speech = measure_lufs(norm_speech)
    del sp_mono
    sp, _ = sf.read(str(norm_speech), dtype="float32")
    sp = np.pad(sp, ((0, max(0, n - len(sp))), (0, 0)))[:n]
    mix = bg                      # built in place: whole-track stereo arrays are the memory peak on long videos
    mix *= env[:, None]
    mix += sp
    del bg, sp
    if background is not None:  # crossfade original -> separated background under the muted parts
        sep, ssr = sf.read(str(background), dtype="float32", always_2d=True)
        if ssr != MIX_SR:
            raise ValueError(f"background is {ssr} Hz, expected {MIX_SR}")
        sep = sep if sep.shape[1] == 2 else np.repeat(sep[:, :1], 2, axis=1)
        sep = np.pad(sep, ((0, max(0, n - len(sep))), (0, 0)))[:n]
        sep *= (1.0 - env)[:, None]
        mix += sep
        del sep
    peak = float(np.abs(mix).max())
    if peak > 0.99:
        mix *= 0.99 / peak
    final_wav = work_dir / "final_audio.wav"
    sf.write(str(final_wav), mix, MIX_SR, subtype="PCM_16")

    # 4. subtitles: shown while the dubbed voice actually sounds
    cues = [(r.get("onset", r["placed_start"]), r.get("voiced_end_at", r["placed_end"]), r[cues_key]) for r in placed]
    srt = write_srt(clip_cues(add_label_cue(cues, f"[{label}]" if label else ""), total), work_dir / "subtitles.srt")

    # 5. mux. The picture is copied, except when trimming (so the cut is frame-accurate) or when the source
    #    codec cannot go into an MP4 (then it is re-encoded to H.264).
    trimmed = total < vdur - 0.01
    codec = media.get("video_codec")
    copyable = codec in MP4_COPY_CODECS
    if reencode is None:
        reencode = trimmed or not copyable
    video_mode = ("re-encoded to h264: " + ("trimmed" if trimmed else f"{codec} cannot be copied into MP4")
                  if reencode else "copied")
    vcodec = X264 if reencode else ["-c:v", "copy", *(["-tag:v", "hvc1"] if codec == "hevc" else [])]
    meta = (["-metadata", f"title={label}: {video.stem}",
             "-metadata", f"comment={label}. Synthetic voice by Bodhan Indic-Speak and translation by Bodhan "
                          "Indic-Translate; it is not the original speaker's voice."] if label else [])
    # Our audio and subtitles start at the container origin; -itsoffset keeps that true if the origin is not 0.
    off = ["-itsoffset", f"{origin:.6f}"] if abs(origin) > 1e-6 else []
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), *off, "-i", str(final_wav),
         *off, "-i", str(srt),
         "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0", *vcodec, "-c:a", "aac", "-b:a", "192k",
         "-c:s", "mov_text", "-metadata:s:s:0", f"language={sub_lang}", "-metadata:s:a:0",
         f"title={label or 'AI-dubbed'}", "-metadata:s:a:0", f"handler_name={label or 'AI-dubbed'}",
         "-metadata:s:a:0", f"language={sub_lang}", *meta,
         "-t", f"{total:.3f}", str(out_mp4)])

    # 6. checks: length, and that video still starts at the same offset relative to the (new) audio
    sd = stream_durations(out_mp4)
    om = probe_media(out_mp4)
    expected_av = media["video_offset"] or 0.0
    got_av = (om["video_start"] or 0.0) - (om["audio_start"] or 0.0)
    report = {
        "output": str(out_mp4), "expected_duration": round(total, 3), "output_duration": round(om["duration"], 3),
        "video_stream_duration": round(sd.get("video", 0), 3), "audio_stream_duration": round(sd.get("audio", 0), 3),
        "length_error_ms": round(abs(om["duration"] - total) * 1000, 1),
        "av_offset_expected_ms": round(expected_av * 1000, 1), "av_offset_output_ms": round(got_av * 1000, 1),
        "av_offset_error_ms": round(abs(got_av - expected_av) * 1000, 1),
        "speech_lufs_before": round(lufs_before, 2), "speech_lufs_after": round(lufs_speech, 2),
        "final_mix_lufs": round(measure_lufs(final_wav), 2), "mix_peak": round(min(peak, 0.99), 3),
        "video": video_mode, "video_codec_in": codec, "label": label or None,
        "segments_placed": len(placed), "muted_original_s": round(sum(b - a for a, b in merge_intervals(mute)), 1),
        "background_under_dub": "separated" if background is not None else "silence",
        "speech_track": str(raw_speech), "final_audio": str(final_wav),
    }
    (work_dir / "assemble_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("assemble: %s", report)
    return report


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    work, lang, video, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    dub = json.loads((work / lang / f"dub.{lang}.json").read_text(encoding="utf-8"))["segments"]
    segs = json.loads((work / lang / f"segments.{lang}.json").read_text(encoding="utf-8"))
    dur = None
    if len(sys.argv) > 5:
        if sys.argv[5] == "auto":  # partial preview: end just after the last dubbed clip
            dur = max(r["placed_end"] for r in dub if "placed_end" in r) + 0.5
        else:
            dur = float(sys.argv[5])
    rep = assemble(dub, segs, video, out, work / lang / "assemble", duration=dur, mute_undubbed=dur is not None)
    print(json.dumps(rep, indent=2))
