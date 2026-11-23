import json

import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.speech_inject import alter_audio, main


def test_audio_mutations_preserve_structure_and_change_duration():
    audio = np.ones((1000, 1), dtype="float32")
    assert len(alter_audio(audio, 100, "omission")) == 500
    assert len(alter_audio(audio, 100, "repetition")) == 2000
    garbled = alter_audio(audio, 100, "garbled")
    assert len(garbled) == 1400
    assert np.all(garbled[500:900] == 0)


def test_speech_inject_cli_writes_labelled_cases_without_overwriting(tmp_path):
    audio = tmp_path / "input.wav"
    sf.write(audio, np.ones(2000, dtype="float32") * 0.1, 1000)
    dataset = tmp_path / "input.jsonl"
    dataset.write_text(json.dumps({"id": "x", "lang": "hi", "source": "hello",
                                   "translation": "नमस्ते", "audio": "input.wav"}),
                       encoding="utf-8")
    out = tmp_path / "cases.jsonl"
    args = ["--dataset", str(dataset), "--out", str(out), "--audio-dir", str(tmp_path / "audio"),
            "--kind", "omission"]
    assert main(args) == 0
    item = json.loads(out.read_text(encoding="utf-8"))
    assert item["labels"][0]["type"] == "speech_omission"
    assert sf.info(tmp_path / item["audio"]).frames == 1000
    assert sf.info(audio).frames == 2000
    with pytest.raises(SystemExit):
        main(args)
