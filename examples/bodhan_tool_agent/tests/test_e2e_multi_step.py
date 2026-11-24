"""End-to-end multi-step integration tests with mocked Bodhan models (Slice 14 / SPEC §13 & §17)."""

import json
from pathlib import Path

import httpx
import pytest

from bodhan_agent.loop import run_task
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient


@pytest.mark.asyncio
async def test_e2e_multi_step_ocr_translate_tts_verify(small_pdf: Path, tmp_path: Path):
    """End-to-end multi-step task: PDF OCR -> Translate -> TTS -> Verification (SPEC §17 step 14)."""
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    pdf_input = inputs_dir / "scanned_doc.pdf"
    pdf_input.write_bytes(small_pdf.read_bytes())

    out_dir = tmp_path / "out_e2e"

    # Dummy WAV payload for TTS response
    dummy_wav_bytes = (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    hindi_md = "# दस्तावेज़ शीर्षक\n\nयह एक नमूना दस्तावेज़ पाठ है।\n\n| आइटम | मात्रा |\n| --- | --- |\n| सेब | 5 |"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = request.content.decode("utf-8", errors="ignore")
        if "/ocr" in url or "indic-ocr" in body:
            return httpx.Response(
                200,
                json={
                    "markdown": "# Document Title\n\nThis is sample document text.\n\n| Item | Qty |\n| --- | --- |\n| Apple | 5 |",
                    "page": 1,
                    "confidence": 0.98,
                },
            )
        elif "/chat/completions" in url:
            return httpx.Response(
                200,
                json={
                    "translated": hindi_md,
                    "source_lang": "en",
                    "target_lang": "hi",
                    "tokens": 42,
                },
            )
        elif "/audio/speech" in url:
            return httpx.Response(
                200,
                content=dummy_wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
        elif "/audio/transcriptions" in url:
            return httpx.Response(
                200,
                json={
                    "text": "दस्तावेज़ शीर्षक यह एक नमूना दस्तावेज़ पाठ है। सेब 5",
                    "language": "hi",
                    "language_confidence": 0.99,
                    "segments": [
                        {"start": 0.0, "end": 2.0, "text": "दस्तावेज़ शीर्षक यह एक नमूना दस्तावेज़ पाठ है। सेब 5"},
                    ],
                },
            )
        return httpx.Response(404, json={"error": f"Not found: {url}"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    result = await run_task(
        task="OCR scanned_doc.pdf, translate to Hindi, and synthesize audio speech",
        files_dir=inputs_dir,
        out_dir=out_dir,
        max_cost_inr=20.0,
        tool_client=bodhan,
    )

    assert result["status"] == "success"
    assert result["cost_inr"] > 0.0
    assert result["time_s"] >= 0.0

    # 1. Verify result.md exists, has links and final answer
    result_md = out_dir / "result.md"
    assert result_md.exists()
    md_text = result_md.read_text(encoding="utf-8")
    assert "## Summary of Actions" in md_text
    assert "## Generated Artifacts" in md_text
    assert ".wav" in md_text

    # 2. Verify report.json structure and verifications
    report_json = out_dir / "report.json"
    assert report_json.exists()
    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    assert report_data["status"] == "success"
    assert len(report_data["steps"]) == 3  # OCR, Translate, Synthesize
    assert len(report_data["verifications"]) >= 2  # translation_coverage, table_parity, or audio_back_transcribe

    check_names = [v["check"] for v in report_data["verifications"]]
    assert "translation_coverage" in check_names
    assert "table_parity" in check_names

    # 3. Verify trace.jsonl has monotonic steps and correct event sequence
    trace_file = out_dir / "trace.jsonl"
    assert trace_file.exists()
    trace_records = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(trace_records) >= 6

    step_nums = [r["step"] for r in trace_records]
    assert step_nums == list(range(1, len(step_nums) + 1))

    types = [r["type"] for r in trace_records]
    assert "resolve" in types
    assert "preflight" in types
    assert "plan" in types
    assert "tool_call" in types
    assert "verification" in types
    assert "final" in types

    # 4. Verify generated artifacts
    artifacts = list((out_dir / "artifacts").iterdir())
    artifact_names = [a.name for a in artifacts]
    assert any(name.endswith("_extracted.md") for name in artifact_names)
    assert any(name.endswith("_translated.txt") for name in artifact_names)
    assert any(name.endswith(".wav") for name in artifact_names)
    assert any(name.endswith(".ai.txt") for name in artifact_names)

    await base_client.close()


@pytest.mark.asyncio
async def test_e2e_multi_step_audio_transcribe_translate_srt(short_wav: Path, tmp_path: Path):
    """End-to-end task: Transcribe Audio -> Translate -> Generate and Validate SRT."""
    inputs_dir = tmp_path / "inputs_srt"
    inputs_dir.mkdir()
    wav_input = inputs_dir / "hindi_speech.wav"
    wav_input.write_bytes(short_wav.read_bytes())

    out_dir = tmp_path / "out_srt"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/audio/transcriptions" in url:
            return httpx.Response(
                200,
                json={
                    "text": "नमस्ते भारत। यह एक परीक्षण है।",
                    "language": "hi",
                    "language_confidence": 0.99,
                    "segments": [
                        {"start": 0.0, "end": 1.5, "text": "नमस्ते भारत।"},
                        {"start": 1.6, "end": 3.0, "text": "यह एक परीक्षण है।"},
                    ],
                },
            )
        elif "/chat/completions" in url:
            return httpx.Response(
                200,
                json={
                    "translated": "Hello India.\nThis is a test.",
                    "source_lang": "hi",
                    "target_lang": "en",
                    "tokens": 15,
                },
            )
        return httpx.Response(404, json={"error": f"Not found: {url}"})

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    result = await run_task(
        task="Transcribe hindi_speech.wav to English SRT",
        files_dir=inputs_dir,
        out_dir=out_dir,
        max_cost_inr=10.0,
        tool_client=bodhan,
    )

    assert result["status"] == "success"

    # Verify SRT artifact was written and verified
    report_json = out_dir / "report.json"
    assert report_json.exists()
    report_data = json.loads(report_json.read_text(encoding="utf-8"))

    check_names = [v["check"] for v in report_data["verifications"]]
    assert "srt_structure" in check_names

    srt_files = list((out_dir / "artifacts").glob("*.srt"))
    assert len(srt_files) >= 1
    srt_contents = [f.read_text(encoding="utf-8") for f in srt_files]
    assert any("-->" in c for c in srt_contents)
    assert any("Hello India" in c or "This is a test" in c for c in srt_contents)

    await base_client.close()
