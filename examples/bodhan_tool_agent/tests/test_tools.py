from __future__ import annotations

import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import fitz
import httpx
import pytest

from bodhan_mcp.config import CostLedger, Settings
from bodhan_mcp.errors import BudgetExceeded, ToolError
from bodhan_mcp.media import parse_srt, write_srt
from bodhan_mcp.tools import ToolService


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000)
    return buffer.getvalue()


def _settings(**values: str) -> Settings:
    return replace(Settings(), **values)


def test_settings_accept_groq_api_key_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "unit-test-groq-key")
    settings = Settings.from_env()
    assert settings.llm_api_key == "unit-test-groq-key"
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"
    assert settings.llm_model == "openai/gpt-oss-20b"


def test_translate_uses_bodhan_route_and_saves_artifact(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ನಮಸ್ಕಾರ"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 8},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=client,
    )
    try:
        result = service.translate_text("Hello there.", "hi")
        assert result["ok"] is True
        assert Path(result["artifact_path"]).is_file()
        assert seen[0].url.path == "/v1/chat/completions"
        request_body = json.loads(seen[0].content)
        assert request_body == {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": "Hello there."}],
            "target_language_code": "hi",
            "target_script": "native",
        }
        assert result["coverage"] == "all source chunks translated"
        assert result["verification"]["status"] == "passed"
        assert result["verification"]["checks"]["chunk_order_preserved"] is True
    finally:
        service.close()


def test_translation_provider_error_is_actionable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_request_error", "message": "Unsupported parameter."}},
        )

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ToolError) as error:
            service.translate_text("Hello there.", "hi")
        assert error.value.code == "model_rejected_input"
        assert error.value.message == "Unsupported parameter."
        assert error.value.details["http_status"] == 400
        assert error.value.details["provider_code"] == "invalid_request_error"
    finally:
        service.close()


def test_transcription_does_not_fabricate_srt_timestamps(tmp_path: Path) -> None:
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(_wav_bytes())
    service = ToolService(
        _settings(transcribe_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"text": "spoken words"})
            )
        ),
    )
    try:
        result = service.transcribe_audio(str(audio_path), "hi", include_segments=True)
        assert result["ok"] is True
        assert "segments_artifact_path" not in result
        assert any("no SRT" in warning for warning in result["warnings"])
    finally:
        service.close()


def test_translate_subtitles_preserves_times_for_generated_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "outside-input-root"
    response_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response_count
        response_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"translated-{response_count}"}}],
                "usage": {"completion_tokens": 2},
            },
        )

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path / "inputs",),
        output_dir=output_dir,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        source_srt = write_srt(
            [
                {"start": 2.0, "end": 4.0, "text": "first caption"},
                {"start": 6.5, "end": 8.0, "text": "second caption"},
            ]
        )
        source_path = Path(service._save_text("source-segments", source_srt, ".srt"))
        result = service.translate_subtitles(str(source_path), "hi")
        assert result["ok"] is True
        assert result["coverage"] == "all subtitle segments translated"
        assert response_count == 2
        translated = parse_srt(Path(result["artifact_path"]).read_text(encoding="utf-8"))
        assert [(part["start"], part["end"]) for part in translated] == [
            (2.0, 4.0),
            (6.5, 8.0),
        ]
        assert [part["text"] for part in translated] == ["translated-1", "translated-2"]
    finally:
        service.close()


def test_provider_detail_error_is_preserved(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ToolError) as error:
            service.translate_text("Hello there.", "hi")
        assert error.value.code == "endpoint_not_found"
        assert error.value.message == "Not Found"
        assert error.value.details["http_status"] == 404
    finally:
        service.close()


@pytest.mark.parametrize("language", ["ks-Deva", "sd-Arab"])
def test_translation_rejects_unsupported_codemix(language: str, tmp_path: Path) -> None:
    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )
    try:
        with pytest.raises(ToolError, match="does not support codemix"):
            service.translate_text("Hello there.", language, "codemix")
    finally:
        service.close()


def test_translation_finish_reason_length_is_reported_as_incomplete(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 2048},
            },
        )

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.translate_text("A source sentence.", "hi")
        assert result["ok"] is False
        assert result["error"]["code"] == "translation_incomplete"
        assert result["coverage"] == "none"
    finally:
        service.close()


def test_budget_is_reserved_before_paid_request(tmp_path: Path) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        ledger=CostLedger(maximum=Decimal("0.001")),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(BudgetExceeded):
            service.translate_text("Hello.", "hi")
        assert request_count == 0
    finally:
        service.close()


def test_ocr_processes_every_pdf_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two-pages.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "First page")
    document.new_page().insert_text((72, 72), "Second page")
    document.save(pdf_path)
    document.close()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"page {request_count}"}}]},
        )

    service = ToolService(
        _settings(ocr_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.ocr_document(str(pdf_path))
        assert request_count == 2
        assert result["processed_pages"] == [1, 2]
        assert result["page_count"] == 2
    finally:
        service.close()


def test_ocr_empty_page_result_is_reported_as_unverified(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two-pages.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "First page")
    document.new_page().insert_text((72, 72), "Second page")
    document.save(pdf_path)
    document.close()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        text = "first page text" if request_count == 1 else ""
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    service = ToolService(
        _settings(ocr_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.ocr_document(str(pdf_path))

        assert not result["ok"]
        assert result["error"]["code"] == "ocr_verification_failed"
        assert result["page_count"] == 2
        assert result["verification"]["checks"]["empty_pages"] == [2]
        assert Path(result["artifact_path"]).is_file()
    finally:
        service.close()


def test_ocr_budget_exhaustion_preserves_processed_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two-pages.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "First page")
    document.new_page().insert_text((72, 72), "Second page")
    document.save(pdf_path)
    document.close()
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "page text"}}]})

    service = ToolService(
        _settings(ocr_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        ledger=CostLedger(maximum=Decimal("0.25")),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.ocr_document(str(pdf_path))
        assert result["ok"] is False
        assert result["error"]["code"] == "budget_exceeded"
        assert result["processed_pages"] == [1]
        assert Path(result["artifact_path"]).is_file()
        assert request_count == 1
    finally:
        service.close()


def test_translation_budget_exhaustion_preserves_completed_chunks(tmp_path: Path) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "translated"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 10},
            },
        )

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        ledger=CostLedger(maximum=Decimal("0.041")),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.translate_text("A" * 2501, "hi")
        assert result["ok"] is False
        assert result["error"]["code"] == "budget_exceeded"
        assert result["coverage"] == "partial"
        assert Path(result["artifact_path"]).is_file()
        assert request_count == 1
    finally:
        service.close()


def test_speech_is_back_transcribed_by_bodhan(tmp_path: Path) -> None:
    expected = "Hello, this is a speech verification."
    speech = _wav_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=speech, headers={"Content-Type": "audio/wav"})
        if request.url.path.endswith("/audio/transcriptions"):
            assert b'name="model"\r\n\r\nindic-transcribe' in request.content
            assert b'name="vad"' not in request.content
            return httpx.Response(200, json={"text": expected})
        raise AssertionError(f"Unexpected route: {request.url.path}")

    service = ToolService(
        _settings(speak_key="unit-test-key", transcribe_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.generate_speech(expected, "en")
        assert result["ok"] is True
        assert result["verification"]["status"] == "passed"
        assert result["verification"]["sequence_similarity"] == 1.0
        assert result["verification"]["failed_checks"] == []
        assert Path(result["artifact_path"]).is_file()
    finally:
        service.close()


def test_speech_match_metrics_detect_reordering_repetition_and_omissions() -> None:
    expected = "one two three four"
    exact = ToolService._text_match_metrics(expected, "one, two three four.")
    reordered = ToolService._text_match_metrics(expected, "one three two four")
    repeated = ToolService._text_match_metrics(expected, "one two three four one two three four")
    omitted = ToolService._text_match_metrics(expected, "one two four")

    assert exact["sequence_similarity"] == 1.0
    assert reordered["sequence_similarity"] < 0.8
    assert repeated["length_ratio"] == 2.0
    assert repeated["repeated_or_extra_tokens"] > 0
    assert omitted["token_coverage"] < 0.8


def test_script_detection_marks_shared_scripts_ambiguous() -> None:
    devanagari = ToolService._detect_indic_language("यह हिन्दी पाठ है")
    telugu = ToolService._detect_indic_language("ఇది తెలుగు")
    english = ToolService._detect_indic_language("English only")

    assert devanagari is not None
    assert devanagari["status"] == "unknown"
    assert devanagari["language"] is None
    assert telugu is not None
    assert telugu["status"] == "probable"
    assert telugu["language"] == "te"
    assert english is not None
    assert english["status"] == "unknown"


def test_ambiguous_paid_timeout_is_not_retried_and_is_costed_conservatively(
    tmp_path: Path,
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("response timed out", request=request)

    service = ToolService(
        _settings(translate_key="unit-test-key", max_retries=3),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ToolError) as error:
            service.translate_text("Hello.", "hi")

        assert error.value.code == "provider_outcome_unknown"
        assert error.value.details["outcome"] == "unknown"
        assert request_count == 1
        assert service._request_count == 1
        assert service._retry_count == 0
        assert service.ledger.reserved == 0
        assert service.ledger.spent > 0
    finally:
        service.close()


def test_provider_server_error_is_not_retried_after_uncertain_completion(
    tmp_path: Path,
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(503, json={"error": {"code": "overloaded"}})

    service = ToolService(
        _settings(translate_key="unit-test-key", max_retries=3),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ToolError) as error:
            service.translate_text("Hello.", "hi")

        assert error.value.code == "provider_outcome_unknown"
        assert error.value.details["http_status"] == 503
        assert request_count == 1
        assert service.ledger.spent > 0
    finally:
        service.close()


def test_cost_ledger_reservations_are_atomic_under_concurrency() -> None:
    ledger = CostLedger(maximum=Decimal("1"))

    def reserve() -> bool:
        try:
            ledger.reserve(Decimal("0.75"))
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve(), range(2)))

    assert sum(results) == 1
    assert ledger.reserved == Decimal("0.75")


def test_text_file_reference_is_read_without_inline_model_text(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A text file.", encoding="utf-8")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "translated"}}]})

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = service.translate_text(None, "hi", text_file_path=str(source))
        assert result["ok"] is True
        assert captured["messages"] == [{"role": "user", "content": "A text file."}]
    finally:
        service.close()


def test_successful_translation_requests_are_cached_within_a_run(tmp_path: Path) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते"}}]})

    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        first = service.translate_text("Hello.", "hi")
        second = service.translate_text("Hello.", "hi")
        assert first["ok"] and second["ok"]
        assert second["cached"] is True
        assert request_count == 1
    finally:
        service.close()


def test_sensitive_text_files_are_rejected(tmp_path: Path) -> None:
    key_file = tmp_path / "keys.txt"
    key_file.write_text("not a real key", encoding="utf-8")
    service = ToolService(
        _settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )
    try:
        with pytest.raises(ToolError, match="Credential/configuration"):
            service.translate_text(None, "hi", text_file_path=str(key_file))
    finally:
        service.close()


def test_missing_model_key_releases_reserved_budget(tmp_path: Path) -> None:
    ledger = CostLedger(maximum=Decimal("1"))
    service = ToolService(
        _settings(),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "out",
        ledger=ledger,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )
    try:
        with pytest.raises(ToolError, match="Configure the environment variable"):
            service.translate_text("Hello.", "hi")
        assert ledger.reserved == Decimal("0")
    finally:
        service.close()
