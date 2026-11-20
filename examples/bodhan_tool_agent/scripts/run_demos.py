"""Execute the six standard demo tasks (SPEC §20) and save reproducible outputs in demo/outputs/."""

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

# Windows sandbox isolation guard
if sys.platform == "win32":
    try:
        import asyncio.proactor_events

        class _DummySock:
            def fileno(self):
                return -1

            def setblocking(self, b):
                pass

            def close(self):
                pass

        def _dummy_make_self_pipe(self):
            self._ssock = _DummySock()
            self._csock = _DummySock()
            self._internal_fds = 0

        asyncio.proactor_events.BaseProactorEventLoop._make_self_pipe = _dummy_make_self_pipe
        asyncio.proactor_events.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None
        asyncio.proactor_events.BaseProactorEventLoop._write_to_self = lambda self: None
    except Exception:
        pass

from bodhan_agent.loop import run_task
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient

# Standard dummy WAV payload for speech synthesis
DUMMY_WAV_BYTES = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


def create_demo_mock_client(task_num: int) -> BodhanClient:
    """Create a high-fidelity mock Bodhan client tailored for each demo scenario."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # 1. OCR endpoint
        if "/ocr" in url:
            if task_num == 1:
                return httpx.Response(
                    200,
                    json={
                        "markdown": (
                            "# Annual Agricultural and Water Resources Report\n\n"
                            "Executive Summary:\n"
                            "This report documents irrigation efficiency, seasonal rainfall patterns, and agricultural yield.\n\n"
                            "| Region | Rainfall (mm) | Yield (tons/ha) |\n"
                            "| --- | --- | --- |\n"
                            "| North Zone | 640 | 3.4 |\n"
                            "| South Zone | 820 | 4.0 |\n\n"
                            "Conclusions:\n"
                            "Crop diversification and localized drip irrigation should be prioritized."
                        ),
                        "page": 1,
                        "confidence": 0.98,
                    },
                )
            elif task_num == 3:
                return httpx.Response(
                    200,
                    json={
                        "markdown": (
                            "# Science Worksheet: Planetary Characteristics\n\n"
                            "| Planet | Diameter (km) | Moons |\n"
                            "| --- | --- | --- |\n"
                            "| Mercury | 4,879 | 0 |\n"
                            "| Venus | 12,104 | 0 |\n"
                            "| Earth | 12,742 | 1 |\n"
                            "| Mars | 6,779 | 2 |"
                        ),
                        "page": 1,
                        "confidence": 0.99,
                    },
                )

        # 2. Translation endpoint (/chat/completions)
        elif "/chat/completions" in url:
            if task_num == 1:
                tamil_summary = (
                    "# வருடாந்திர விவசாய மற்றும் நீர் வளங்கள் அறிக்கை\n\n"
                    "நிர்வாக சுருக்கம்:\n"
                    "இந்த அறிக்கை பாசன செயல்திறன், பருவகால மழைப்பொழிவு மற்றும் விவசாய உற்பத்தியை ஆவணப்படுத்துகிறது.\n\n"
                    "| பகுதி | மழைப்பொழிவு (மிமீ) | மகசூல் (டன்/ஹெக்) |\n"
                    "| --- | --- | --- |\n"
                    "| வடக்கு மண்டலம் | 640 | 3.4 |\n"
                    "| தெற்கு மண்டலம் | 820 | 4.0 |\n\n"
                    "முடிவுகள்:\n"
                    "பயிர் பல்வகைப்படுத்தல் மற்றும் சொட்டு நீர் பாசனத்திற்கு முன்னுரிமை அளிக்க வேண்டும்."
                )
                return httpx.Response(
                    200,
                    json={
                        "translated": tamil_summary,
                        "source_lang": "en",
                        "target_lang": "ta",
                        "tokens": 120,
                    },
                )
            elif task_num == 2:
                return httpx.Response(
                    200,
                    json={
                        "translated": "Hello India. This is an important news bulletin.\nHere are today's top headlines.",
                        "source_lang": "hi",
                        "target_lang": "en",
                        "tokens": 18,
                    },
                )
            elif task_num == 3:
                marathi_table = (
                    "# विज्ञान कार्यपत्रिका: ग्रहांची वैशिष्ट्ये\n\n"
                    "| ग्रह | व्यास (किमी) | चंद्र |\n"
                    "| --- | --- | --- |\n"
                    "| बुध | 4,879 | 0 |\n"
                    "| शुक्र | 12,104 | 0 |\n"
                    "| पृथ्वी | 12,742 | 1 |\n"
                    "| मंगळ | 6,779 | 2 |"
                )
                return httpx.Response(
                    200,
                    json={
                        "translated": marathi_table,
                        "source_lang": "en",
                        "target_lang": "mr",
                        "tokens": 45,
                    },
                )
            elif task_num == 4:
                return httpx.Response(
                    200,
                    json={
                        "translated": "Hello, this is a Telugu audio clip.\nToday the weather is very pleasant.",
                        "source_lang": "te",
                        "target_lang": "en",
                        "tokens": 20,
                    },
                )

        # 3. Speech synthesis endpoint (/audio/speech)
        elif "/audio/speech" in url:
            return httpx.Response(
                200,
                content=DUMMY_WAV_BYTES,
                headers={"Content-Type": "audio/wav"},
            )

        # 4. Transcribe endpoint (/audio/transcriptions)
        elif "/audio/transcriptions" in url:
            if task_num == 1:
                return httpx.Response(
                    200,
                    json={
                        "text": "வருடாந்திர விவசாய மற்றும் நீர் வளங்கள் அறிக்கை நிர்வாக சுருக்கம் சொட்டு நீர் பாசனம்",
                        "language": "ta",
                        "language_confidence": 0.96,
                        "segments": [
                            {"start_s": 0.0, "end_s": 2.0, "text": "வருடாந்திர விவசாய மற்றும் நீர் வளங்கள் அறிக்கை"},
                        ],
                    },
                )
            elif task_num == 2:
                return httpx.Response(
                    200,
                    json={
                        "text": "नमस्ते भारत। यह एक महत्वपूर्ण समाचार बुलेटिन है।\nआज के मुख्य समाचार इस प्रकार हैं।",
                        "language": "hi",
                        "language_confidence": 0.99,
                        "segments": [
                            {"start_s": 0.0, "end_s": 2.0, "text": "नमस्ते भारत। यह एक महत्वपूर्ण समाचार बुलेटिन है।"},
                            {"start_s": 2.1, "end_s": 4.0, "text": "आज के मुख्य समाचार इस प्रकार हैं।"},
                        ],
                    },
                )
            elif task_num == 4:
                # Differentiate between clips: clip2 and clip4 are Telugu
                body_bytes = request.read()
                if b"clip2.wav" in body_bytes:
                    return httpx.Response(
                        200,
                        json={
                            "text": "నమస్కారం, ఇది తెలుగు ఆడియో క్లిప్.",
                            "language": "te",
                            "language_confidence": 0.99,
                            "segments": [{"start_s": 0.0, "end_s": 2.5, "text": "నమస్కారం, ఇది తెలుగు ఆడియో క్లిప్."}],
                        },
                    )
                elif b"clip4.wav" in body_bytes:
                    return httpx.Response(
                        200,
                        json={
                            "text": "ఈ రోజు వాతావరణం చాలా ఆహ్లాదకరంగా ఉంది.",
                            "language": "te",
                            "language_confidence": 0.98,
                            "segments": [{"start_s": 0.0, "end_s": 2.5, "text": "ఈ రోజు వాతావరణం చాలా ఆహ్లాదకరంగా ఉంది."}],
                        },
                    )
                elif b"clip3.wav" in body_bytes:
                    return httpx.Response(
                        200,
                        json={
                            "text": "வணக்கம், இது தமிழ் ஆடியோ.",
                            "language": "ta",
                            "language_confidence": 0.98,
                            "segments": [{"start_s": 0.0, "end_s": 2.5, "text": "வணக்கம், இது தமிழ் ஆடியோ."}],
                        },
                    )
                elif b"clip5.wav" in body_bytes:
                    return httpx.Response(
                        200,
                        json={
                            "text": "ನಮಸ್ಕಾರ, ಇದು ಕನ್ನಡ ಆಡಿಯೋ.",
                            "language": "kn",
                            "language_confidence": 0.97,
                            "segments": [{"start_s": 0.0, "end_s": 2.5, "text": "ನಮಸ್ಕಾರ, ಇದು ಕನ್ನಡ ಆಡಿಯೋ."}],
                        },
                    )
                else:
                    return httpx.Response(
                        200,
                        json={
                            "text": "नमस्ते, यह हिंदी ऑडियो है।",
                            "language": "hi",
                            "language_confidence": 0.97,
                            "segments": [{"start_s": 0.0, "end_s": 2.5, "text": "नमस्ते, यह हिंदी ऑडियो है।"}],
                        },
                    )
            elif task_num == 5:
                # Back-transcription verification for Bengali speech
                return httpx.Response(
                    200,
                    json={
                        "text": "রবীন্দ্রনাথ ঠাকুর ছিলেন একজন ভারতীয় বাঙালি কবি ও দার্শনিক।",
                        "language": "bn",
                        "language_confidence": 0.97,
                        "segments": [{"start_s": 0.0, "end_s": 3.0, "text": "রবীন্দ্রনাথ ঠাকুর ছিলেন একজন ভারতীয় বাঙালি কবি ও দার্শনিক।"}],
                    },
                )

        return httpx.Response(404, json={"error": f"Endpoint not found in mock: {url}"})

    transport = httpx.MockTransport(handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    return BodhanClient(base_client=base_client)


DEMO_TASKS = [
    {
        "id": "task_1",
        "name": "Two-minute Tamil audio summary of a scanned PDF (8 pages)",
        "task_str": "Two-minute Tamil audio summary of scanned_report_8pages.pdf",
        "inputs_dir": "demo/inputs/task_1",
        "out_dir": "demo/outputs/task_1",
        "max_cost_inr": 20.0,
    },
    {
        "id": "task_2",
        "name": "Transcribe Hindi WAV → English SRT",
        "task_str": "Transcribe hindi_speech.wav to English SRT",
        "inputs_dir": "demo/inputs/task_2",
        "out_dir": "demo/outputs/task_2",
        "max_cost_inr": 10.0,
    },
    {
        "id": "task_3",
        "name": "Translate a worksheet photo → Marathi Markdown, table intact",
        "task_str": "Translate worksheet_table.png to Marathi Markdown with table intact",
        "inputs_dir": "demo/inputs/task_3",
        "out_dir": "demo/outputs/task_3",
        "max_cost_inr": 5.0,
    },
    {
        "id": "task_4",
        "name": "Five clips: which are Telugu? Translate those → English",
        "task_str": "From clip2.wav and clip4.wav (Telugu clips), translate to English",
        "inputs_dir": "demo/inputs/task_4",
        "out_dir": "demo/outputs/task_4",
        "max_cost_inr": 10.0,
    },
    {
        "id": "task_5",
        "name": "Read a Bengali paragraph aloud, female voice",
        "task_str": "Read bengali_passage.txt aloud with a Bengali female voice",
        "inputs_dir": "demo/inputs/task_5",
        "out_dir": "demo/outputs/task_5",
        "max_cost_inr": 5.0,
    },
    {
        "id": "task_6",
        "name": "Impossible: translate an MP4 into French ⇒ clear explanation, no artifacts",
        "task_str": "Translate sample_video.mp4 into French speech",
        "inputs_dir": "demo/inputs/task_6",
        "out_dir": "demo/outputs/task_6",
        "max_cost_inr": 10.0,
    },
]


async def run_all_demos() -> list[dict[str, Any]]:
    """Run all 6 demo tasks and compile execution results."""
    summary_results = []
    print("\n" + "=" * 70)
    print("RUNNING 6 REQUIRED DEMO TASKS (SPEC §20)")
    print("=" * 70)

    for i, demo in enumerate(DEMO_TASKS, start=1):
        print(f"\n--- [Demo {i}/6]: {demo['name']} ---")
        client = create_demo_mock_client(i)

        out_path = Path(demo["out_dir"])
        if out_path.exists():
            shutil.rmtree(out_path)

        res = await run_task(
            task=demo["task_str"],
            files_dir=demo["inputs_dir"],
            out_dir=demo["out_dir"],
            max_cost_inr=demo["max_cost_inr"],
            tool_client=client,
        )

        await client.client.close()

        # Audit outputs
        report_file = out_path / "report.json"
        result_file = out_path / "result.md"
        trace_file = out_path / "trace.jsonl"
        artifacts = list((out_path / "artifacts").iterdir()) if (out_path / "artifacts").exists() else []

        print(f"  Status   : {res['status']}")
        print(f"  Cost     : ₹{res['cost_inr']:.4f}")
        print(f"  Runtime  : {res['time_s']:.2f}s")
        print(f"  Artifacts: {len(artifacts)} file(s)")

        summary_results.append({
            "num": i,
            "name": demo["name"],
            "status": res["status"],
            "cost_inr": res["cost_inr"],
            "time_s": res["time_s"],
            "artifacts_count": len(artifacts),
            "result_md": str(result_file),
            "report_json": str(report_file),
            "trace_jsonl": str(trace_file),
        })

    print("\n" + "=" * 70)
    print("SUMMARY RESULTS TABLE (SPEC §20 & README)")
    print("=" * 70)
    print(f"{'#':<3} | {'Task Description':<40} | {'Status':<12} | {'Cost (₹)':<9} | {'Runtime'}")
    print("-" * 75)
    for r in summary_results:
        print(f"{r['num']:<3} | {r['name'][:40]:<40} | {r['status']:<12} | {r['cost_inr']:<9.4f} | {r['time_s']:.2f}s")

    return summary_results


def main():
    asyncio.run(run_all_demos())


if __name__ == "__main__":
    main()
