"""Output contract and engineering checks added 2026-11-22:
- every input container the assignment names (MP4, MOV, MKV, WebM) gives a playable MP4, whatever its video codec
- the MP4 and the subtitles say the dub is AI-made
- segments.<lang>.json has the audio durations and the full QA result
- each run writes a structured JSON-lines log, tagged with language and segment
All local: ffmpeg and fakes, no API calls."""

import json
import logging
import subprocess
import threading

import numpy as np
import soundfile as sf

from bodhan_dub import logs
from bodhan_dub import pipeline as P
from bodhan_dub.assemble import assemble
from bodhan_dub.audio import probe_media
from bodhan_dub.dub_language import DubConfig
from bodhan_dub.subtitles import LABEL_MAX, add_label_cue

from test_parallel_languages import _fake_stages


# ---------------------------------------------------------------- AI-dubbed notice in the subtitles
def test_label_gets_its_own_cue_when_there_is_room():
    cues = add_label_cue([(2.5, 4.0, "पहला"), (5.0, 6.0, "दूसरा")], "[AI-dubbed (Hindi)]")
    assert cues[0] == (0.0, 2.4, "[AI-dubbed (Hindi)]") and cues[1:] == [(2.5, 4.0, "पहला"), (5.0, 6.0, "दूसरा")]
    late = add_label_cue([(9.0, 10.0, "x")], "[L]")
    assert late[0] == (0.0, LABEL_MAX, "[L]")


def test_label_joins_the_first_cue_when_speech_starts_at_once():
    cues = add_label_cue([(0.3, 2.0, "पहला"), (3.0, 4.0, "दूसरा")], "[L]")
    assert cues == [(0.3, 2.0, "[L]\nपहला"), (3.0, 4.0, "दूसरा")]   # no line is hidden or moved


def test_no_label_and_no_cues():
    assert add_label_cue([(1.0, 2.0, "a")], "") == [(1.0, 2.0, "a")]
    assert add_label_cue([], "[L]") == [(0.0, LABEL_MAX, "[L]")]


# ---------------------------------------------------------------- containers and codecs
def _make(path, vcodec, acodec, audio_delay=0.0):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=162x120:rate=25:d=4",
                    "-itsoffset", str(audio_delay), "-f", "lavfi", "-i", "sine=frequency=300:duration=3.5",
                    "-map", "0:v", "-map", "1:a", *vcodec, *acodec, str(path)], check=True)


def _streams(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name:stream_tags=name,handler_name,language:format_tags=title,comment",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _dub(tmp_path, video, label="AI-dubbed (Hindi)"):
    clip = tmp_path / "clip.wav"
    sf.write(clip, (np.random.default_rng(0).standard_normal(24000) * 0.2).astype("float32"), 24000)
    recs = [{"id": 1, "clip_path": str(clip), "placed_start": 1.5, "placed_end": 2.5, "onset": 1.5,
             "voiced_end_at": 2.5, "text": "परीक्षा"}]
    return assemble(recs, [{"id": 1, "start": 1.5, "end": 2.5}], video, tmp_path / "out.mp4", tmp_path / "asm",
                    label=label)


def test_webm_vp8_is_re_encoded_keeps_the_av_offset_and_is_labelled(tmp_path):
    # VP8 cannot go into an MP4: before the fix the mux failed ("Invalid argument"). Odd width 162 checks the
    # even-size scale; the audio starts 0.5 s after the video.
    src = tmp_path / "in.webm"
    _make(src, ["-c:v", "libvpx", "-b:v", "200k"], ["-c:a", "libvorbis"], audio_delay=0.5)
    assert probe_media(src)["video_codec"] == "vp8"
    rep = _dub(tmp_path, src)
    assert rep["video"].startswith("re-encoded to h264") and rep["video_codec_in"] == "vp8"
    assert rep["length_error_ms"] <= 50 and rep["av_offset_error_ms"] <= 50
    info = _streams(tmp_path / "out.mp4")
    by_type = {s["codec_type"]: s for s in info["streams"]}
    assert by_type["video"]["codec_name"] == "h264"
    a_tags = by_type["audio"]["tags"]   # ffmpeg 8 also reads the title back as "name"; Debian's 5.1 only as handler_name
    assert a_tags["handler_name"] == "AI-dubbed (Hindi)" and a_tags.get("name", "AI-dubbed (Hindi)") == "AI-dubbed (Hindi)"
    assert a_tags["language"] == "hin"
    assert info["format"]["tags"]["title"] == "AI-dubbed (Hindi): in"
    assert "not the original speaker" in info["format"]["tags"]["comment"]
    srt = (tmp_path / "asm" / "subtitles.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,400\n[AI-dubbed (Hindi)]\n")
    assert "2\n00:00:01,500 --> 00:00:02,500\nपरीक्षा" in srt


def test_mov_prores_is_re_encoded(tmp_path):
    src = tmp_path / "in.mov"
    _make(src, ["-c:v", "prores_ks"], ["-c:a", "pcm_s16le"])
    rep = _dub(tmp_path, src)
    assert rep["video"].startswith("re-encoded to h264") and rep["length_error_ms"] <= 50
    assert {s["codec_type"]: s["codec_name"] for s in _streams(tmp_path / "out.mp4")["streams"]}["video"] == "h264"


def test_mkv_h264_is_copied_untouched(tmp_path):
    src = tmp_path / "in.mkv"
    _make(src, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "scale=160:120"], ["-c:a", "flac"])
    rep = _dub(tmp_path, src)
    assert rep["video"] == "copied" and rep["length_error_ms"] <= 50 and rep["av_offset_error_ms"] <= 50


# ---------------------------------------------------------------- segments.<lang>.json
SEG = {"id": 3, "start": 10.0, "end": 13.5, "src": "The angle is ninety degrees.", "status": "ok"}
REC = {"id": 3, "status": "ok", "text": "कोण नब्बे डिग्री है।", "en": SEG["src"], "clip_dur": 3.3214,
       "onset": 10.02, "voiced_end_at": 13.21, "tts_voiced": 3.6, "qa_attempts": 1,
       "qa": {"cer": 0.031, "ok": True, "flags": [], "ins_ratio": 0.0, "del_ratio": 0.031, "hyp": "कोण नब्बे डिग्री है"}}


def test_segment_has_audio_duration_and_full_qa_result():
    out = P._segment_out(SEG, REC, DubConfig(lang="hi"))
    assert out["source_text"] == SEG["src"] and out["text"] == REC["text"]
    assert out["source_duration_s"] == 3.5 and out["audio_duration_s"] == 3.321
    assert out["speech_duration_s"] == 3.19 and out["tts_duration_s"] == 3.6
    assert out["qa"] == {"result": "pass", "cer": 0.031, "flags": [], "insert_ratio": 0.0, "delete_ratio": 0.031,
                         "back_transcript": "कोण नब्बे डिग्री है", "attempts": 1}
    assert out["qa_cer"] == 0.031


def test_segment_qa_states():
    failed = {**REC, "status": "qa_failed", "qa": {**REC["qa"], "ok": False, "flags": ["repeat"]}}
    assert P._segment_out(SEG, failed, DubConfig())["qa"]["result"] == "fail"
    assert P._segment_out(SEG, {**REC, "qa": None}, DubConfig())["qa"]["result"] == "unknown"
    assert P._segment_out(SEG, REC, DubConfig(qa=False))["qa"] == {"result": "off"}
    skipped = P._segment_out(SEG, {"id": 3, "status": "skipped"}, DubConfig())
    assert skipped["qa"] is None and skipped["audio_duration_s"] is None


# ---------------------------------------------------------------- structured run log
def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_log_records_carry_stage_language_segment_and_data(tmp_path):
    h = logs.attach_run_log(tmp_path)
    try:
        def worker():
            logs.LANG.set("ta")
            logs.SEG.set(7)
            logging.getLogger("bodhan_dub.dub_language").info("dub seg=7", extra={"data": {"speed": 1.1}})

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        logging.getLogger("bodhan_dub.transcribe").warning("chunk %d cut off", 4)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("bodhan_dub.pipeline").exception("language bn failed", extra={"lang": "bn"})
    finally:
        logs.detach_run_log(h)
    a, b, c = _lines(tmp_path / logs.LOG_NAME)
    assert (a["stage"], a["lang"], a["seg"], a["data"]) == ("dub", "ta", 7, {"speed": 1.1})
    assert (b["stage"], b["level"], b["lang"], b["seg"], b["msg"]) == ("asr", "WARNING", None, None, "chunk 4 cut off")
    assert c["lang"] == "bn" and "ValueError: boom" in c["error"]
    assert h not in logging.getLogger().handlers


def test_a_run_writes_its_log_even_when_a_language_fails(tmp_path, monkeypatch):
    _fake_stages(monkeypatch, tmp_path)
    rep = P.run(tmp_path / "v.mp4", ["hi", "ta"], tmp_path / "out", separation="off", align=False)
    lines = _lines(tmp_path / "out" / logs.LOG_NAME)
    assert rep["log"] == logs.LOG_NAME
    assert lines[0]["msg"].startswith("run start") and lines[0]["data"]["langs"] == ["hi", "ta"]
    fail = [x for x in lines if x["level"] == "ERROR"]
    assert fail and fail[0]["lang"] == "ta" and "ta broke" in fail[0]["error"]
    assert any(x["msg"].startswith("segment") and x["lang"] == "hi" for x in lines)
    assert lines[-1]["msg"].startswith("done") and lines[-1]["data"]["status"] == {"hi": "ok", "ta": "failed"}


# ---------------------------------------------------------------- long videos: memory-light mute envelope
def _mute_envelope_whole_track(n, sr, intervals, fade):
    """The earlier whole-track version (2026-11-20), kept as the reference."""
    from bodhan_dub.assemble import merge_intervals
    mask = np.ones(n, dtype="float64")
    for a, b in merge_intervals(intervals):
        mask[max(0, int(a * sr)) : min(n, int(b * sr))] = 0.0
    k = max(1, int(fade * sr))
    cs = np.concatenate([[0.0], np.cumsum(mask)])
    lo = np.clip(np.arange(n) - k // 2, 0, n)
    hi = np.clip(np.arange(n) + k - k // 2, 0, n)
    return ((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1)).astype("float32")


def test_local_mute_envelope_equals_the_whole_track_version():
    from bodhan_dub.assemble import mute_envelope
    rng = np.random.default_rng(1)
    sr, fade = 1000, 0.04
    cases = [[], [(-1.0, 0.01)], [(9.995, 12.0)], [(2.0, 2.0)], [(3.0, 3.01), (3.03, 3.5)], [(1.0, 2.0), (1.5, 3.0)]]
    for _ in range(200):
        m = int(rng.integers(1, 12))
        starts = np.sort(rng.uniform(-0.5, 10.5, m))
        ivs = [(float(a), float(a + rng.choice([0.002, 0.03, 0.3, 2.0]))) for a in starts]
        cases.append([(a, b) for a, b in ivs if b > 0])   # see the next test for intervals that end before 0
    for iv in cases:
        np.testing.assert_allclose(mute_envelope(10 * sr, sr, iv, fade), _mute_envelope_whole_track(10 * sr, sr, iv, fade),
                                   rtol=0, atol=1e-6, err_msg=str(iv))


def test_an_interval_that_ends_before_the_start_mutes_nothing():
    # The whole-track version sliced mask[0:negative], which muted almost the whole track. The pipeline never
    # makes such intervals (segment ends are >= 0), but the envelope must not depend on that.
    from bodhan_dub.assemble import mute_envelope
    assert mute_envelope(10_000, 1000, [(-0.4, -0.1)]).min() == 1.0
