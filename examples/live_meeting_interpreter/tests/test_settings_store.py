import logging

import pytest

from app.config import read_env
from app.settings_store import (
    Form, changed, is_typing_key, locked_keys, my_language_codes, their_language_codes, tk_key_to_ptt,
    update_env_text, validate, write_env,
)
from app.stats import LatencyStats

ENV = (
    "# ---- Bodhan.AI ----\r\n"
    "BODHAN_TRANSLATE_KEY=sk-secret-123\r\n"
    "MY_LANGUAGE=en          # you speak this; subtitles appear in it\r\n"
    "# TTS_VOICE=Kavya       # defaults to a voice recorded in THEIR_LANGUAGE\r\n"
    "MIC_DEVICE=\r\n"
    "BODHAN_TRANSLATE_RPM=15"  # no trailing newline
)


def test_update_keeps_secrets_comments_and_line_endings():
    out = update_env_text(ENV, {"MY_LANGUAGE": "hi", "MIC_DEVICE": "Microphone Array (Realtek(R) Audio)",
                                "TTS_VOICE": "Amit"})
    lines = out.split("\r\n")
    assert lines[1] == "BODHAN_TRANSLATE_KEY=sk-secret-123"
    assert lines[2] == "MY_LANGUAGE=hi          # you speak this; subtitles appear in it"
    assert lines[3].startswith("# TTS_VOICE=Kavya")  # commented-out lines are left alone...
    assert lines[4] == 'MIC_DEVICE="Microphone Array (Realtek(R) Audio)"'
    assert lines[5] == "BODHAN_TRANSLATE_RPM=15"
    assert lines[6] == "TTS_VOICE=Amit"               # ...and the key is appended instead
    assert out.endswith("\r\n")


def test_written_env_reads_back(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8", newline="")
    write_env(path, {"MY_LANGUAGE": "ta", "MIC_DEVICE": 'Odd "name" #1', "TTS_STYLE": ""})
    env = read_env(str(path), environ={})
    assert (env["MY_LANGUAGE"], env["MIC_DEVICE"], env["TTS_STYLE"]) == ("ta", 'Odd "name" #1', "")
    assert env["BODHAN_TRANSLATE_KEY"] == "sk-secret-123"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]  # no temp file left behind


def test_only_managed_keys_can_be_written(tmp_path):
    with pytest.raises(ValueError):
        write_env(tmp_path / ".env", {"BODHAN_TRANSLATE_KEY": "x"})


def test_read_env_lets_the_shell_win_without_touching_os_environ(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ASR_BACKEND=hosted\nMY_LANGUAGE=en\n", encoding="utf-8")
    monkeypatch.delenv("MY_LANGUAGE", raising=False)
    env = read_env(str(path), environ={"ASR_BACKEND": "local"})
    assert (env["ASR_BACKEND"], env["MY_LANGUAGE"]) == ("local", "en")
    import os
    assert "MY_LANGUAGE" not in os.environ
    assert locked_keys({"ASR_BACKEND": "local", "PATH": "x"}) == ["ASR_BACKEND"]


@pytest.mark.parametrize("keysym,char,expected", [
    ("Control_R", "", "ctrl_r"), ("Alt_R", "", "alt_r"), ("F9", "", "f9"), ("Scroll_Lock", "", "scroll_lock"),
    ("a", "a", "a"), ("A", "A", "a"), ("space", " ", None), ("Return", "\r", None), ("F25", "", None),
])
def test_tk_keys_map_to_push_to_talk_names(keysym, char, expected):
    assert tk_key_to_ptt(keysym, char) == expected


def test_typing_keys_are_flagged():
    assert is_typing_key("a") and not is_typing_key("ctrl_r")


def form(**kw) -> Form:
    base = dict(my_language="en", their_language="hi", subtitle_script="native", voice="", style="",
                ptt_key="ctrl_r", mic_device="", cable_device="CABLE Input", monitor_tts=False,
                asr_backend="hosted", live_captions=True, local_model_dir="nowhere")
    base.update(kw)
    return Form(**base)


def test_valid_form_becomes_env_updates():
    updates, problems = validate(form(voice="Amit", monitor_tts=True, live_captions=False))
    assert problems == []
    assert updates["TTS_VOICE"] == "Amit" and updates["MONITOR_TTS"] == "true"
    assert updates["LIVE_CAPTION_INTERVAL_S"] == "0" and updates["TTS_STYLE"] == ""


def test_form_problems_are_explained():
    _, problems = validate(form(their_language="en", mic_device="CABLE Output (VB-Audio Virtual Cable)",
                                ptt_key="nonsense", asr_backend="local"))
    text = " ".join(problems)
    assert "two different languages" in text and "VB-Cable" in text
    assert "push-to-talk" in text and "model downloaded" in text


def test_only_real_changes_are_saved():
    from tests.test_client import settings
    s = settings(tts_voice="Kavya", cable_device="CABLE Input", live_caption_interval_s=1.5)
    updates, _ = validate(form(cable_device="CABLE Input (VB-Audio Virtual Cable)", live_captions=True))
    assert changed(updates, s) == {}  # same devices by substring, automatic voice, live still on
    updates, _ = validate(form(their_language="ta", voice="Kavya", live_captions=False))
    assert changed(updates, s) == {"THEIR_LANGUAGE": "ta", "TTS_VOICE": "Kavya", "LIVE_CAPTION_INTERVAL_S": "0"}


def test_dubbing_needs_a_device_subtitles_dont_listen_to():
    speakers = "Speakers (Realtek(R) Audio)"
    ok, problems = validate(form(dub_incoming=True, dub_device="Headphones (Realtek(R) Audio)",
                                 captured_output=speakers))
    assert problems == [] and ok["DUB_INCOMING"] == "true" and ok["DUB_DEVICE"].startswith("Headphones")
    for device, why in (("", "Choose where"), (speakers, "default output"),
                        ("CABLE Input (VB-Audio Virtual Cable)", "meeting would hear it"),
                        ("CABLE In 16ch (VB-Audio Virtual Cable)", "meeting would hear it")):
        _, problems = validate(form(dub_incoming=True, dub_device=device, captured_output=speakers))
        assert any(why in p for p in problems), (device, problems)
    _, problems = validate(form(dub_incoming=False, dub_device=speakers, captured_output=speakers))
    assert problems == []  # switched off: the device isn't used


def test_dub_settings_only_saved_when_changed():
    from tests.test_client import settings
    s = settings(dub_incoming=False, dub_device="Headphones", dub_voice="Kavya")  # Kavya = automatic for en
    updates, _ = validate(form(dub_device="Headphones (Realtek(R) Audio)", dub_voice=""))
    assert not {k for k in changed(updates, s) if k.startswith("DUB_")}
    updates, _ = validate(form(dub_incoming=True, dub_device="Headphones (Realtek(R) Audio)",
                               dub_voice="Amit", captured_output="Speakers"))
    assert {k: v for k, v in changed(updates, s).items() if k.startswith("DUB_")} == {
        "DUB_INCOMING": "true", "DUB_VOICE": "Amit"}


def test_language_lists():
    assert "hi" in their_language_codes() and "bho" not in my_language_codes()  # bho: ASR only
    assert set(their_language_codes()) <= set(my_language_codes())


def test_latency_stats_reads_the_pipeline_log_lines():
    stats = LatencyStats(keep=2)
    log = logging.getLogger("test.stats")
    log.addHandler(stats)
    log.setLevel(logging.INFO)
    try:
        for e2e in (1.0, 2.0, 4.0):
            log.info("in #1 2.0s audio | asr 0.30s mt 0.20s | e2e %.2fs | partial 0.30s | a -> b", e2e)
        log.info("out #1 1.0s audio | asr 0.30s mt 0.20s tts 1.10s | e2e 1.60s | 1 part(s) | a -> b")
        log.info("unrelated")
    finally:
        log.removeHandler(stats)
    snap = stats.snapshot()
    assert snap["in"][0] == 3 and snap["in"][1]["e2e"] == 3.0  # median of the last 2
    assert snap["out"][1]["tts"] == 1.1
