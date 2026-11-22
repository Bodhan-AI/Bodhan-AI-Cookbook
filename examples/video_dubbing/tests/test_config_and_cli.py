import argparse

import pytest

from bodhan_dub.cli import EXIT_USAGE, build_parser, main, parse_langs
from bodhan_dub.config import Settings
from bodhan_dub.languages import TARGET_LANGS, default_voice


def test_per_model_keys_fall_back_to_shared_key():
    s = Settings.from_env({"BODHAN_API_KEY": "shared", "BODHAN_SPEAK_KEY": "speak-only"})
    assert (s.transcribe_key, s.translate_key, s.speak_key) == ("shared", "shared", "speak-only")


def test_base_url_is_normalised_and_placeholders_ignored():
    s = Settings.from_env({"BODHAN_BASE_URL": "https://api.bodhan.ai/v1/", "LLM_MODEL": "<model name>"})
    assert s.base_url == "https://api.bodhan.ai"
    assert s.llm_model == ""
    assert "LLM_MODEL" in s.missing()


def test_parse_langs_dedupes_and_trims():
    assert parse_langs(" hi,ta, bn ,hi,") == ["hi", "ta", "bn"]


@pytest.mark.parametrize("bad", ["en", "fr", "hi,xx", " , "])
def test_parse_langs_rejects_bad_input(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_langs(bad)


def test_every_target_has_a_voice():
    assert len(TARGET_LANGS) == 22
    for lang in TARGET_LANGS:
        assert default_voice(lang, "female") and default_voice(lang, "male")


def test_parser_rejects_unsupported_container(tmp_path):
    clip = tmp_path / "talk.avi"
    clip.write_bytes(b"x")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", str(clip), "--langs", "hi", "--out", str(tmp_path / "o")])


def test_main_exits_with_usage_error_when_keys_missing(tmp_path, monkeypatch):
    for name in ("BODHAN_API_KEY", "BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY",
                 "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # so load_dotenv() finds no .env
    clip = tmp_path / "talk.mp4"
    clip.write_bytes(b"x")
    assert main(["--input", str(clip), "--langs", "hi", "--out", str(tmp_path / "o")]) == EXIT_USAGE
