import sys
from unittest.mock import MagicMock, patch

from bodhan_dub.cli import parse_args
from bodhan_dub.clients.speak import BodhanSpeakClient
from bodhan_dub.clients.translate import BodhanTranslateClient
from bodhan_dub.config import BodhanConfig


def test_cli_parse_args(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bodhan_dub",
            "--input", "demo.mp4",
            "--langs", "hi,ta,bn",
            "--out", "./outputs",
            "--glossary", "configs/glossary.example.yaml",
            "--voices", "configs/voices.example.yaml",
            "--code-mixed",
            "--mock",
        ],
    )
    args = parse_args()
    assert str(args.input) == "demo.mp4"
    assert args.langs == "hi,ta,bn"
    assert args.code_mixed is True
    assert args.mock is True


def test_13_production_languages_and_voices():
    expected = {"hi", "ta", "bn", "te", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur", "sa"}
    assert expected.issubset(set(BodhanConfig.SUPPORTED_LANGUAGES.keys()))
    for lang in expected:
        assert lang in BodhanConfig.DEFAULT_VOICES


def test_translate_client_uses_cookbook_endpoint_and_handles_error():
    client = BodhanTranslateClient(api_key="dummy-test-key", base_url="https://api.bodhan.ai", mock_mode=False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"translation": "नमस्ते दुनिया", "target_language": "hi"}

    with patch.object(client, "post", return_value=mock_resp) as mock_post:
        res = client.translate("Hello world", target_lang="hi", code_mixed=True)
        assert res == "नमस्ते दुनिया"
        mock_post.assert_called_once()
        endpoint_called = mock_post.call_args.args[0]
        payload = mock_post.call_args.kwargs["json_data"]
        assert endpoint_called == "translate"
        assert payload["script"] == "codemix"
        assert payload["target_language"] == "hi"


def test_speak_client_bounded_retry_fallback_on_429(tmp_path):
    client = BodhanSpeakClient(api_key="dummy-test-key", base_url="https://api.bodhan.ai", mock_mode=False)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "rate limited"

    out_wav = tmp_path / "fallback.wav"
    with patch("time.sleep", return_value=None), patch.object(client, "post", return_value=mock_resp) as mock_post:
        path, dur = client.generate_speech("नमस्ते", "hi", out_wav, target_duration=1.5)
        assert mock_post.call_count == 2  # bounded, no infinite loop
        assert path.exists()
        assert dur == 1.5
