"""End-to-end tests against the offline fake Bodhan API: layout, targets, recovery, isolation, caching."""
import json

import soundfile as sf
from conftest import FakeLLM
from fake_bodhan import FakeBodhan

from bodhan_dub import audio, config

S1 = "[hi] this is spoken sentence number 1."


def segs(out, lang):
    return json.loads((out / lang / f"segments.{lang}.json").read_text())["segments"]


def test_smoke_layout_and_targets(run, video):
    r = run()
    assert r.rc == 0
    for f in ("report.json", "transcript.en.json", "hi/video.hi.mp4", "hi/subtitles.hi.srt", "hi/segments.hi.json",
              "ta/video.ta.mp4", "ta/subtitles.ta.srt", "run.log.jsonl"):
        assert (r.out / f).exists(), f
    rep = r.report
    assert rep["ai_dubbed"] and set(rep["languages"]) == {"hi", "ta"}
    for lang in ("hi", "ta"):
        L = rep["languages"][lang]
        assert L["status"] == "ok" and L["misses"] == []
        assert L["targets"]["output_length_diff_ms"] <= 100
        assert abs(L["targets"]["speech_lufs"] + 16) <= 1
        assert L["targets"]["sync_within_300ms_pct"] == 100.0
        assert L["cost"]["total_cost_inr"] > 0
    out_dur = audio.probe_duration(r.out / "hi" / "video.hi.mp4")
    assert abs(out_dur - audio.probe_duration(video)) < 0.1


def test_smoke_30_second_clip_three_languages(run, video30):
    """The assignment's end-to-end smoke test: a 30 s clip, three languages, every target checked."""
    r = run(langs=("hi", "ta", "bn"), clip30=True, llm=FakeLLM())
    assert r.rc == 0 and r.report["minutes_of_video"] == 0.5
    for lang in ("hi", "ta", "bn"):
        L = r.report["languages"][lang]
        assert L["segments"]["total"] == 4 and L["segments"]["failed"] == 0
        assert (r.out / lang / f"video.{lang}.mp4").stat().st_size > 0
        assert abs(audio.probe_duration(r.out / lang / f"video.{lang}.mp4") - 30.0) < 0.1
    assert r.report["source"]["grouping"] == "llm" and r.report["source"]["glossary_terms"] == 1


def test_music_bed_is_kept_where_nobody_speaks(run):
    r = run(langs=("hi",))
    x, sr = sf.read(str(r.out / "work" / "hi" / "mix.wav"), dtype="float32")
    quiet = x[int(4.6 * sr):int(5.6 * sr)]      # gap between sentence 1 and 2: only the music bed (0.05 amplitude)
    assert 0.01 < float(abs(quiet).max()) < 0.12


def test_repeat_is_detected_and_recovered(run):
    r = run(langs=("hi",), fake=FakeBodhan(repeat_once={S1}))
    s0 = segs(r.out, "hi")[0]
    assert s0["retries"]["tts"] >= 1
    assert s0["status"] == "dubbed" and s0["qa"]["ok"]
    assert any("repeat" in a["flags"] for a in s0["attempts"])


def test_omission_is_detected_and_recovered(run):
    r = run(langs=("hi",), fake=FakeBodhan(truncate_once={S1}))
    s0 = segs(r.out, "hi")[0]
    assert s0["retries"]["tts"] >= 1 and s0["qa"]["ok"]
    assert any("omission" in a["flags"] for a in s0["attempts"])


def test_long_translation_is_condensed_not_sped_up(run):
    r = run(langs=("hi",), fake=FakeBodhan(chars_per_sec=3.0), llm=FakeLLM())
    for s in segs(r.out, "hi"):
        assert s["retries"]["condense"] >= 1
        assert s["stretch"] <= 1.2 and s["english_used"] == "Short one."
    assert r.report["languages"]["hi"]["retries"]["condense"] >= 3


def test_without_llm_long_lines_degrade_gracefully_and_are_reported(run):
    r = run(langs=("hi",), fake=FakeBodhan(chars_per_sec=3.0))
    assert r.rc == 0
    L = r.report["languages"]["hi"]
    assert L["segments"]["degraded"] == 3 and any(m["target"] == "speed_change_or_fidelity" for m in L["misses"])
    assert all(s["stretch"] <= 1.2 for s in segs(r.out, "hi"))
    assert (r.out / "hi" / "video.hi.mp4").exists()


def test_one_failing_language_does_not_stop_the_others(run):
    r = run(fake=FakeBodhan(fail_langs={"ta"}))
    assert r.rc == 1
    assert r.report["languages"]["hi"]["status"] == "ok"
    assert r.report["languages"]["ta"]["status"] in ("partial", "failed")
    assert (r.out / "hi" / "video.hi.mp4").exists() and (r.out / "report.json").exists()


def test_non_speech_is_skipped_with_a_reason_and_never_dubbed(run):
    fake = FakeBodhan(stt_text=lambda n: "" if n == 2 else f"this is spoken sentence number {n}.")
    r = run(langs=("hi",), fake=fake)
    en = json.loads((r.out / "transcript.en.json").read_text())
    assert len(en["segments"]) == 2
    assert any(s["reason"] == "no_speech_transcribed" for s in en["skipped"])
    assert len(segs(r.out, "hi")) == 2
    assert r.report["languages"]["hi"]["segments"]["skipped_non_speech"] == 1


def test_rerun_uses_cache_and_makes_no_new_paid_calls(run, tmp_path):
    out = tmp_path / "cached"
    fake = FakeBodhan()
    run(langs=("hi",), fake=fake, out=out)
    first = dict(fake.calls)
    r2 = run(langs=("hi",), fake=fake, out=out)
    assert fake.calls == first and r2.rc == 0
    assert r2.report["languages"]["hi"]["cost"]["total_cost_inr"] == 0


def test_user_glossary_rendering_is_used_in_translation_input(run, tmp_path):
    g = tmp_path / "g.yaml"
    g.write_text("terms:\n  - term: sentence\n    translations: {hi: 'वाक्य'}\n")
    r = run(langs=("hi",), glossary=g)
    assert any("वाक्य" in t for t in r.fake.mt_log)
    assert not any("sentence" in t for t in r.fake.mt_log)


def test_voices_file_overrides_voice(run, tmp_path):
    v = tmp_path / "v.yaml"
    v.write_text("speakers: {S1: Zed}\n")
    r = run(langs=("hi",), voices=v)
    assert {s["voice"] for s in segs(r.out, "hi")} == {"Zed"}


def test_manual_override_text_is_used_verbatim(run, tmp_path):
    out = tmp_path / "ov"
    (out / "hi").mkdir(parents=True)
    (out / "hi" / "overrides.json").write_text(json.dumps({"1": {"text": "मेरा अपना अनुवाद"}}))
    r = run(langs=("hi",), out=out)
    s1 = segs(r.out, "hi")[1]
    assert s1["translation"] == "मेरा अपना अनुवाद" and "manual_text" in s1["notes"]


def test_llm_is_optional(run):
    assert run(langs=("hi",), llm=None).rc == 0


def test_style_reaches_indic_speak_and_none_overrides_project_style(run, tmp_path):
    out = tmp_path / "st"
    (out / "hi").mkdir(parents=True)
    (out / "hi" / "overrides.json").write_text(json.dumps({"1": {"style": "none"}, "2": {"style": "sad"}}))
    r = run(langs=("hi",), out=out, style="anger")
    by_idx = {s["idx"]: s for s in segs(r.out, "hi")}
    assert by_idx[0]["style"] == "anger" and by_idx[1]["style"] is None and by_idx[2]["style"] == "sad"
    styles = {t["text"]: t["instr"].get("style") for t in r.fake.tts_log}
    assert styles[by_idx[0]["translation"]] == "anger"
    assert "style" not in next(t["instr"] for t in r.fake.tts_log if t["text"] == by_idx[1]["translation"])
    assert styles[by_idx[2]["translation"]] == "sad"
    assert all(t["instr"]["lang"] == "hi" for t in r.fake.tts_log)


def test_custom_voice_replaces_tts_for_that_line_and_is_fitted(run, tmp_path):
    import numpy as np
    import soundfile as sf
    out = tmp_path / "cv"
    (out / "hi" / "custom").mkdir(parents=True)
    sf.write(str(out / "hi" / "custom" / "seg1.wav"), (0.3 * np.sin(2 * np.pi * 300 * np.arange(24000 * 2) / 24000)).astype("float32"), 24000)
    (out / "hi" / "overrides.json").write_text(json.dumps({"1": {"custom_audio": "custom/seg1.wav"}}))
    r = run(langs=("hi",), out=out)
    s1 = segs(r.out, "hi")[1]
    assert s1["custom_voice"] is True and s1["voice"] == "Your voice" and s1["status"] in ("dubbed", "degraded")
    assert s1["translation"] and "custom_voice" in s1["notes"] and s1["stretch"] <= config.STRETCH_MAX
    assert s1["translation"] not in [t["text"] for t in r.fake.tts_log]          # Indic-Speak was not asked to voice it
    assert (r.out / "work" / "hi" / "seg1.wav").exists() and r.rc == 0


def test_missing_ffmpeg_fails_first_with_a_clear_message_and_spends_nothing(run, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = run(langs=("hi",))
    assert res.rc == 2
    msg = res.report["errors"][0]
    assert "ffmpeg" in msg and "ffprobe" in msg and "PATH" in msg and "ffmpeg -version" in msg
    assert sum(res.fake.calls.values()) == 0                                # no Bodhan call was made
