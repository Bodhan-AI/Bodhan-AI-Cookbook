"""End-to-end tests for bodhan_agent with FakeBodhanTransport + ScriptedFakeLLM."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_agent.config import RunConfig
from bodhan_agent.executor import run_agent
from bodhan_agent.mcp_bridge import InProcessMCPBridge
from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.tools import BodhanToolkit

from tests.fakes import (
    FakeBodhanState,
    ScriptedFakeLLM,
    make_fake_bodhan_transport,
    make_segmented_wav,
    make_test_pdf,
)


def _bridge(tmp_path: Path, state: FakeBodhanState | None = None) -> tuple[InProcessMCPBridge, FakeBodhanState]:
    transport, st = make_fake_bodhan_transport(state)
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
    )
    tk = BodhanToolkit(client=client, work_dir=tmp_path / "work")
    return InProcessMCPBridge(tk), st


async def test_e2e_multistep_pdf_to_tamil_audio_summary(tmp_path: Path) -> None:
    pdf = make_test_pdf(tmp_path / "chapter.pdf", page_count=4)
    tamil_summary = (
        "இந்த அத்தியாயம் நீர் பாதுகாப்பு மற்றும் விவசாயம் பற்றி விளக்குகிறது. "
        "கிராம மக்கள் மழைநீர் சேகரிப்பு மூலம் நிலத்தடி நீரை உயர்த்தினர்."
    )
    st = FakeBodhanState(
        ocr_pages=[f"# पृष्ठ {i + 1}\n\nयह अध्याय जल संरक्षण के बारे में है।" for i in range(4)],
        translate_map={"ta": tamil_summary},
        transcribe_by_lang={"ta": tamil_summary},
        speak_duration_s=11.0,  # Realistic ~12 chars/sec for ~130 chars, within ±20% of 12s target.
    )
    bridge, _ = _bridge(tmp_path, st)

    llm = ScriptedFakeLLM(
        [
            # Epoch A -> TaskSpec
            {
                "summary": "Create a concise Tamil audio summary and Tamil summary text of the scanned chapter PDF.",
                "deliverables": [
                    {
                        "id": "d_audio",
                        "kind": "audio",
                        "language_raw": "Tamil",
                        "output_filename": "tamil_summary.mp3",
                        "constraints": {"duration_s": 12, "target_words": 25, "format": "mp3"},
                    },
                    {
                        "id": "d_text",
                        "kind": "text",
                        "language_raw": "Tamil",
                        "output_filename": "tamil_summary.txt",
                        "constraints": {},
                    },
                ],
                "inputs_used": [str(pdf.resolve())],
                "assumptions": ["Used default female Tamil voice (Anitha)."],
                "needs_probe": [],
                "infeasible_reason": None,
            },
            # Epoch B -> Flat step list
            {
                "steps": [
                    {"id": "s1", "tool": "ocr_document", "args": {"path": str(pdf.resolve())}, "rationale": "OCR PDF pages"},
                    {"id": "s2", "tool": "translate_text", "args": {"path": "$s1.text_path", "target_language": "en"}, "rationale": "Translate OCR text to English"},
                    {"id": "s3", "tool": "q_summarize_en", "args": {"text_ref": "$s2.text_path", "target_words": 240}, "rationale": "Quarantined English summary"},
                    {"id": "s4", "tool": "translate_text", "args": {"path": "$s3.text_path", "target_language": "ta"}, "rationale": "Translate summary to Tamil"},
                    {"id": "s5", "tool": "synthesize_speech", "args": {"path": "$s4.text_path", "language": "ta", "format": "mp3"}, "deliverable_id": "d_audio"},
                    {"id": "s6", "tool": "verify_speech", "args": {"audio_path": "$s5.audio_path", "language": "ta", "chunk_manifest_path": "$s5.chunk_manifest_path"}, "deliverable_id": "d_audio"},
                    {"id": "s7", "tool": "publish_artifact", "args": {"deliverable_id": "d_audio", "source_ref": "$s5.audio_path", "filename": "tamil_summary.mp3"}},
                    {"id": "s8", "tool": "publish_artifact", "args": {"deliverable_id": "d_text", "source_ref": "$s4.text_path", "filename": "tamil_summary.txt"}},
                ],
                "final_answer_template": "Produced a verified 2-minute Tamil audio summary and text transcript from the 4-page PDF.",
                "assumptions": [],
            },
            # Q-LLM summarize_en output (strictly English!)
            {"summary_en": "This chapter explains community water conservation and sustainable agriculture practices across rural villages."},
        ]
    )

    out_dir = tmp_path / "out"
    cfg = RunConfig(
        task="Make a two-minute Tamil audio summary of this chapter",
        input_paths=[pdf],
        out_dir=out_dir,
        max_steps=30,
        max_cost_inr=20.0,
    )
    report = await run_agent(cfg, llm=llm, bridge=bridge)

    assert report.status == "success"
    assert report.total_cost_inr > 0
    assert (out_dir / "result.md").is_file()
    assert (out_dir / "report.json").is_file()
    assert (out_dir / "trace.jsonl").is_file()
    assert (out_dir / "trace.html").is_file()
    assert (out_dir / "artifacts" / "tamil_summary.mp3").is_file()
    assert (out_dir / "artifacts" / "tamil_summary.txt").is_file()
    assert all(v.passed for v in report.verification)
    result_md = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "AI-Generated Audio Disclosure" in result_md


async def test_e2e_telugu_clip_filtering_with_probe_and_foreach(tmp_path: Path) -> None:
    clips = [
        make_segmented_wav(tmp_path / "clip1.wav", [("tone", 2.0)]),
        make_segmented_wav(tmp_path / "clip2.wav", [("tone", 2.0)]),
        make_segmented_wav(tmp_path / "clip3.wav", [("tone", 2.0)]),
    ]
    st = FakeBodhanState(
        transcribe_texts=[
            "ఇది తెలుగు వార్తల క్లిప్.",  # probe clip1 -> te
            "यह हिंदी आवाज संदेश है।",   # probe clip2 -> hi
            "ఇది మరొక తెలుగు క్లిప్.",  # probe clip3 -> te
            "ఇది తెలుగు వార్తల క్లిప్.",  # full transcribe clip1
            "ఇది మరొక తెలుగు క్లిప్.",  # full transcribe clip3
        ]
    )
    bridge, _ = _bridge(tmp_path, st)

    llm = ScriptedFakeLLM(
        [
            {
                "summary": "Identify Telugu clips and translate them into English.",
                "deliverables": [
                    {
                        "id": "d1",
                        "kind": "markdown",
                        "language_raw": "English",
                        "output_filename": "telugu_translations.md",
                        "constraints": {"filter_language": "Telugu"},
                    }
                ],
                "inputs_used": [str(c.resolve()) for c in clips],
                "assumptions": [],
                "needs_probe": [{"kind": "detect_language", "path": str(c.resolve())} for c in clips],
                "infeasible_reason": None,
            },
            {
                "steps": [
                    {
                        "id": "asr",
                        "foreach": {"over": "probe_facts", "filter": {"language": "te"}, "as": "clip"},
                        "tool": "transcribe_audio",
                        "args": {"path": "$clip.path", "language": "te"},
                    },
                    {
                        "id": "tr",
                        "foreach": {"over": "probe_facts", "filter": {"language": "te"}, "as": "clip"},
                        "tool": "translate_text",
                        "args": {"path": "$asr.text_path", "target_language": "en"},
                    },
                    {
                        "id": "merge",
                        "tool": "merge_text_artifacts",
                        "args": {"inputs": ["$tr.text_path"], "labels": ["clip1.wav", "clip3.wav"], "filename": "telugu_translations.md"},
                    },
                    {
                        "id": "pub",
                        "tool": "publish_artifact",
                        "args": {"deliverable_id": "d1", "source_ref": "$merge.text_path", "filename": "telugu_translations.md"},
                    },
                ],
                "final_answer_template": "Detected 2 Telugu clips (clip1.wav, clip3.wav) and translated them into English.",
                "assumptions": [],
            },
        ]
    )

    out_dir = tmp_path / "out_te"
    cfg = RunConfig(
        task="Which of these clips are in Telugu? Translate those into English",
        input_paths=clips,
        out_dir=out_dir,
    )
    report = await run_agent(cfg, llm=llm, bridge=bridge)
    assert report.status == "success"
    md = (out_dir / "artifacts" / "telugu_translations.md").read_text(encoding="utf-8")
    assert "clip1.wav" in md and "clip3.wav" in md and "clip2.wav" not in md


async def test_e2e_impossible_french_task_costs_zero_and_writes_no_artifacts(tmp_path: Path) -> None:
    clip = make_segmented_wav(tmp_path / "video_audio.wav", [("tone", 5.0)])
    bridge, st = _bridge(tmp_path)
    # Even with an empty ScriptedFakeLLM (simulating LLM not needed / down), pre-LLM gate catches it at ₹0!
    llm = ScriptedFakeLLM([])
    out_dir = tmp_path / "out_fr"
    cfg = RunConfig(
        task="Translate this video into French",
        input_paths=[clip],
        out_dir=out_dir,
    )
    report = await run_agent(cfg, llm=llm, bridge=bridge)
    assert report.status == "infeasible"
    assert report.total_cost_inr == 0.0
    assert len(st.calls) == 0
    assert report.artifacts == []
    assert "French" in (report.reason or "")
    assert (out_dir / "result.md").is_file()
    assert json.loads((out_dir / "report.json").read_text(encoding="utf-8"))["total_cost_inr"] == 0.0


async def test_e2e_budget_exhaustion_stops_cleanly_with_partial_results(tmp_path: Path) -> None:
    pdf = make_test_pdf(tmp_path / "two_page.pdf", page_count=2)  # OCR floor = ₹0.40
    bridge, _ = _bridge(tmp_path)
    # Set budget ₹0.45: enough for OCR (₹0.40) + translate (~₹0.001), NOT enough for TTS (~₹0.60)!
    llm = ScriptedFakeLLM(
        [
            {
                "summary": "OCR and synthesize Tamil speech",
                "deliverables": [
                    {"id": "d_text", "kind": "text", "language_raw": "Tamil", "output_filename": "out.txt", "constraints": {}},
                    {"id": "d_audio", "kind": "audio", "language_raw": "Tamil", "output_filename": "out.mp3", "constraints": {}},
                ],
                "inputs_used": [str(pdf.resolve())],
                "assumptions": [],
                "needs_probe": [],
                "infeasible_reason": None,
            },
            {
                "steps": [
                    {"id": "s1", "tool": "ocr_document", "args": {"path": str(pdf.resolve())}},
                    {"id": "s2", "tool": "translate_text", "args": {"path": "$s1.text_path", "target_language": "ta"}},
                    {"id": "s3", "tool": "publish_artifact", "args": {"deliverable_id": "d_text", "source_ref": "$s2.text_path", "filename": "out.txt"}},
                    {"id": "s4", "tool": "synthesize_speech", "args": {"path": "$s2.text_path", "language": "ta", "format": "mp3"}},
                    {"id": "s5", "tool": "publish_artifact", "args": {"deliverable_id": "d_audio", "source_ref": "$s4.audio_path", "filename": "out.mp3"}},
                ],
                "final_answer_template": "Completed Tamil translation and speech.",
                "assumptions": [],
            },
        ]
    )
    # Make Tamil text long enough (~1200 chars -> ₹0.72 TTS) so runtime reservation stops before TTS!
    bridge.toolkit.client._transport, _ = make_fake_bodhan_transport(
        FakeBodhanState(translate_map={"ta": "இது தமிழ் வாக்கியம். " * 70})
    )
    out_dir = tmp_path / "out_budget"
    cfg = RunConfig(
        task="Translate this PDF into Tamil text and audio",
        input_paths=[pdf],
        out_dir=out_dir,
        max_cost_inr=0.45,
    )
    report = await run_agent(cfg, llm=llm, bridge=bridge)
    assert report.status == "partial"
    assert report.total_cost_inr <= 0.45 + 1e-9
    assert (out_dir / "artifacts" / "out.txt").is_file()
    assert not (out_dir / "artifacts" / "out.mp3").exists()
    assert "budget" in (report.reason or "").lower()


async def test_e2e_epoch_c_replan_recovers_after_verify_speech_failure(tmp_path: Path) -> None:
    """Proves bounded Epoch C targeted re-plan recovers when initial audio fails repetition check."""
    txt = tmp_path / "bn.txt"
    bn_text = "এটি একটি বাংলা পরীক্ষা বাক্য।"
    txt.write_text(bn_text, encoding="utf-8")

    st = FakeBodhanState(
        transcribe_texts=[
            "এটি বাংলা এটি বাংলা এটি বাংলা এটি বাংলা এটি বাংলা",  # Attempt 1: repetition loop -> verify fails!
            bn_text,  # Attempt 2 (after Epoch C replan): clean back-transcription -> verify passes!
        ],
        speak_duration_s=2.0,
    )
    bridge, _ = _bridge(tmp_path, st)
    bridge.toolkit.client.cache.enabled = False

    llm = ScriptedFakeLLM(
        [
            # Epoch A
            {
                "summary": "Read Bengali text aloud with female voice",
                "deliverables": [
                    {
                        "id": "d1",
                        "kind": "audio",
                        "language_raw": "Bengali",
                        "output_filename": "bn.mp3",
                        "constraints": {"voice_gender": "female", "format": "mp3"},
                    }
                ],
                "inputs_used": [str(txt.resolve())],
                "assumptions": [],
                "needs_probe": [],
                "infeasible_reason": None,
            },
            # Epoch B
            {
                "steps": [
                    {"id": "s1", "tool": "synthesize_speech", "args": {"path": str(txt.resolve()), "language": "bn", "gender": "female", "format": "mp3"}},
                    {"id": "s2", "tool": "verify_speech", "args": {"audio_path": "$s1.audio_path", "language": "bn", "chunk_manifest_path": "$s1.chunk_manifest_path"}},
                    {"id": "s3", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$s1.audio_path", "filename": "bn.mp3"}},
                ],
                "final_answer_template": "Synthesized Bengali audio.",
                "assumptions": [],
            },
            # Epoch C (targeted repair after verify_speech detected repetition!)
            {
                "steps": [
                    {"id": "r1_sp", "tool": "synthesize_speech", "args": {"path": str(txt.resolve()), "language": "bn", "voice": "Ishita", "format": "mp3"}},
                    {"id": "r1_ver", "tool": "verify_speech", "args": {"audio_path": "$r1_sp.audio_path", "language": "bn", "chunk_manifest_path": "$r1_sp.chunk_manifest_path"}},
                    {"id": "r1_pub", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$r1_sp.audio_path", "filename": "bn.mp3"}},
                ],
                "final_answer_template": "Re-synthesized and verified Bengali audio after Epoch C recovery.",
                "assumptions": [],
            },
        ]
    )

    out_dir = tmp_path / "out_replan"
    cfg = RunConfig(
        task="Read this paragraph aloud in Bengali with a female voice",
        input_paths=[txt],
        out_dir=out_dir,
    )
    report = await run_agent(cfg, llm=llm, bridge=bridge)
    assert report.status == "success"
    assert all(v.passed for v in report.verification)
    # Confirm Epoch C was invoked.
    assert any(c["role_tag"] == "p_llm_epoch_c_round_1" for c in llm.recorded_calls)


def test_render_final_answer_interpolates_safe_metadata_handles() -> None:
    from bodhan_agent.executor import AgentOrchestrator

    rendered = AgentOrchestrator._render_final_answer(
        "Audio duration: {s1.duration_s}s, cues: $s2.cue_count, file: $s3.artifact_path.",
        {
            "s1": {"duration_s": 110.3},
            "s2": {"cue_count": 42},
            "s3": {"artifact_path": "/tmp/out/artifacts/subtitles_en.srt"},
        },
    )
    assert rendered == "Audio duration: 110.3s, cues: 42, file: subtitles_en.srt."

