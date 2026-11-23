"""The offline stub enforces the real API's request rules, so client bugs fail offline too."""

from __future__ import annotations

import json
from collections.abc import Iterator

import numpy as np
import pytest
import requests

from bodhan_eval import audio as A
from bodhan_eval.testing.stub_server import SAMPLE_RATE, StubServer, _parse_multipart, speech_like, speech_like_wav

AUTH = {"Authorization": "Bearer stub-key"}


@pytest.fixture
def stub() -> Iterator[StubServer]:
    fixture = {
        "translate": {"en": {"पानी": "water"}, "hi": {"Long text.": {"text": "लंबा", "truncated": True}}},
        "transcribe": {"hi": "पानी उबलता है"},
        "chat": [{"contains": "SOURCE", "content": '{"errors": []}'}],
    }
    with StubServer(fixture) as s:
        yield s


def post(stub: StubServer, path: str, **kw) -> requests.Response:
    return requests.post(stub.url + path, timeout=10, **kw)


def test_translate_lookup_and_echo_default(stub: StubServer) -> None:
    r = post(stub, "/translate", json={"text": "पानी", "target_language": "en"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["translation"] == "water"
    r = post(stub, "/translate", json={"text": "unknown", "target_language": "en"}, headers=AUTH)
    assert r.json()["translation"] == "unknown"
    assert stub.misses == [{"kind": "translate", "language": "en", "text": "unknown"}]


def test_translate_truncated_flag_from_fixture(stub: StubServer) -> None:
    r = post(stub, "/translate", json={"text": "Long text.", "target_language": "hi"}, headers=AUTH)
    assert r.json()["truncated"] is True


@pytest.mark.parametrize("field", ["temperature", "max_tokens", "stream"])
def test_translate_rejects_llm_style_fields(stub: StubServer, field: str) -> None:
    r = post(stub, "/translate", json={"text": "x", "target_language": "hi", field: 0}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request_error"


def test_missing_key_is_401_with_error_envelope(stub: StubServer) -> None:
    r = post(stub, "/translate", json={"text": "x", "target_language": "hi"})
    assert r.status_code == 401
    assert set(r.json()["error"]) == {"message", "code", "request_id"}


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param(
            {"model": "indic-speak", "input": "नमस्ते", "voice": "Kavya", "instructions": '{"lang": "hi"}'}, 200, id="ok"
        ),
        pytest.param(
            {"model": "indic-speak", "input": "", "voice": "Kavya", "instructions": '{"lang": "hi"}'}, 422, id="empty"
        ),
        pytest.param({"model": "indic-speak", "input": "x", "instructions": '{"lang": "hi"}'}, 422, id="no-voice"),
        pytest.param(
            {"model": "other", "input": "x", "voice": "Kavya", "instructions": '{"lang": "hi"}'}, 422, id="model"
        ),
        pytest.param(
            {"model": "indic-speak", "input": "x", "voice": "Kavya", "instructions": "hi"}, 422, id="instructions"
        ),
    ],
)
def test_speech_request_rules(stub: StubServer, body: dict, status: int) -> None:
    r = post(stub, "/v1/audio/speech", json=body, headers=AUTH)
    assert r.status_code == status
    if status == 200:
        x, sr = A.load_audio_bytes(r.content)
        assert sr == SAMPLE_RATE and len(x) > 0


def _wav(seconds: float) -> bytes:
    return A.wav_bytes(speech_like(seconds), SAMPLE_RATE)


def test_transcription_multipart_and_30s_limit(stub: StubServer) -> None:
    data = {"model": "indic-transcribe", "language": "hi"}
    ok = post(
        stub, "/v1/audio/transcriptions", files={"file": ("a.wav", _wav(5), "audio/wav")}, data=data, headers=AUTH
    )
    assert ok.status_code == 200 and ok.json() == {"text": "पानी उबलता है"}
    long = post(
        stub, "/v1/audio/transcriptions", files={"file": ("a.wav", _wav(31), "audio/wav")}, data=data, headers=AUTH
    )
    assert long.status_code == 400
    assert [r["status"] for r in stub.requests] == [200, 400]


def test_chat_completions_openai_shape(stub: StubServer) -> None:
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "SOURCE: x"}],
        "response_format": {"type": "json_object"},
    }
    r = post(stub, "/v1/chat/completions", json=body)
    out = r.json()
    assert out["choices"][0]["message"]["content"] == '{"errors": []}'
    assert out["usage"]["prompt_tokens"] >= 0
    assert stub.requests[-1]["response_format"] == "json_object"


def test_faults_fire_the_requested_number_of_times(stub: StubServer) -> None:
    stub.add_fault("/translate", 429, times=2, retry_after="0.01")
    codes = [
        post(stub, "/translate", json={"text": "पानी", "target_language": "en"}, headers=AUTH).status_code
        for _ in range(3)
    ]
    assert codes == [429, 429, 200]


def test_fault_can_match_body_substring(stub: StubServer) -> None:
    stub.add_fault("/translate", 500, times=-1, contains="boom")
    assert post(stub, "/translate", json={"text": "boom", "target_language": "en"}, headers=AUTH).status_code == 500
    assert post(stub, "/translate", json={"text": "fine", "target_language": "en"}, headers=AUTH).status_code == 200


def test_fixture_file_and_faults_load(tmp_path) -> None:
    path = tmp_path / "fx.json"
    path.write_text(json.dumps({"faults": [{"path": "/transliterate", "status": 503}]}), encoding="utf-8")
    with StubServer(path) as s:
        r = requests.post(s.url + "/transliterate", json={"text": "a", "language": "hi"}, headers=AUTH, timeout=10)
        assert r.status_code == 503


def test_speech_like_audio_is_clean_for_the_waveform_checks() -> None:
    x = speech_like(6.0)
    st = A.analyse(x, SAMPLE_RATE)
    assert float(np.abs(x).max()) < 0.9
    assert st.clip_ratio == 0.0
    assert st.noise_runs == []
    assert st.longest_silence is None or st.longest_silence[1] - st.longest_silence[0] < 1.2


def test_speech_like_wav_is_deterministic_and_scales_with_text() -> None:
    assert speech_like_wav("नमस्ते दुनिया") == speech_like_wav("नमस्ते दुनिया")
    short, _ = A.load_audio_bytes(speech_like_wav("x" * 13))
    long, _ = A.load_audio_bytes(speech_like_wav("x" * 130))
    assert len(long) > 5 * len(short) / 2


def test_parse_multipart_reads_text_and_file_fields() -> None:
    req = requests.Request("POST", "http://x", files={"file": ("a.wav", b"\x00\x01", "audio/wav")}, data={"model": "m"})
    prepared = req.prepare()
    assert isinstance(prepared.body, bytes)
    fields = _parse_multipart(prepared.headers["Content-Type"], prepared.body)
    assert fields == {"model": b"m", "file": b"\x00\x01"}
