"""Regression unit tests reproducing the 4 reviewer-found bugs with FakeBodhanTransport.

Each test reproduces the failure mode faithfully before the fix and verifies the generic fix after:
  - BUG 1: Cache blocks Epoch C recovery; `attempt` busts cache & `only_chunks` re-synthesizes only failed chunks.
  - BUG 2: Markdown tables/syntax spoken literally; deterministic Markdown -> speakable text + provenance + verify_speech.
  - BUG 3: indic-translate Indic->Indic returning English; automatic Bodhan English pivot + wrong_output_script guard (both Indic and en targets).
  - BUG 4: Reused --out directory retains stale artifacts; cleans only agent outputs, never unrelated files.
"""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_agent.config import RunConfig
from bodhan_agent.executor import run_agent
from bodhan_agent.mcp_bridge import InProcessMCPBridge
from bodhan_agent.store import ArtifactStore
from bodhan_mcp.cache import DiskCache
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.text_utils import compute_cer_spoken, script_ratio_for_language
from bodhan_mcp.tools import BodhanToolkit

from tests.fakes import FakeBodhanState, ScriptedFakeLLM, make_fake_bodhan_transport


def _toolkit(tmp_path: Path, state: FakeBodhanState | None = None) -> tuple[BodhanToolkit, FakeBodhanState]:
    transport, st = make_fake_bodhan_transport(state)
    client = BodhanClient(
        cache=DiskCache(tmp_path / "cache"),
        transport=transport,
        base_backoff_s=0.005,
        max_retries=0,
    )
    return BodhanToolkit(client=client, work_dir=tmp_path / "work"), st


# ---------------------------------------------------------------------------
# BUG 1: Recovery dead due to cache; attempt & only_chunks selective repair
# ---------------------------------------------------------------------------
async def test_bug1_epoch_c_recovery_busts_cache_automatically_and_resynthesizes_failed_chunks(tmp_path: Path) -> None:
    """With disk cache ENABLED, Epoch C must force fresh model samples without relying on LLM to set `attempt`."""
    txt = tmp_path / "two_chunks.txt"
    chunk0 = (
        "பள்ளி அறிவியல் ஆய்வகத்திற்கான பொருட்கள் பட்டியல் மிகவும் தெளிவாகவும் முழுமையாகவும் தயாரிக்கப்பட்டுள்ளது. "
        "மாணவர்கள் ஒவ்வொரு கருவியையும் பாதுகாப்பாகப் பயன்படுத்தி ஆய்வுகளைச் செய்ய வேண்டும்."
    )
    chunk1 = (
        "கண்ணாடி பீக்கர், டிஜிட்டல் தெர்மோமீட்டர் மற்றும் சோலார் செல் கிட் ஆகியவை புதிய பருவத்திற்கு வாங்கப்பட்டுள்ளன. "
        "ஆசிரியர்கள் அனைத்து அளவுகளையும் சரிபார்த்து குறிப்பேட்டில் பதிவு செய்வார்கள்."
    )
    txt.write_text(f"{chunk0}\n\n{chunk1}", encoding="utf-8")

    # Attempt 0: chunk 0 passes back-transcription; chunk 1 fails with severe repetition/garble.
    # Attempt 1 (Epoch C): chunk 1 is re-synthesized fresh and back-transcribes cleanly!
    st = FakeBodhanState(
        transcribe_texts=[
            chunk0,  # verify round 0, chunk 0 -> PASS
            "தவறான உரை தவறான உரை தவறான உரை தவறான உரை தவறான உரை",  # verify round 0, chunk 1 -> FAIL
            chunk0,  # verify round 1, chunk 0 -> PASS (or cached)
            chunk1,  # verify round 1, chunk 1 -> PASS
        ],
        speak_duration_s=10.0,
        speak_distinct_per_call=True,
    )
    tk, _ = _toolkit(tmp_path, st)
    assert tk.client.cache.enabled is True
    bridge = InProcessMCPBridge(tk)

    # Notice Epoch C re-emits synthesize_speech with the EXACT SAME args (no `attempt` or `only_chunks` from LLM!).
    llm = ScriptedFakeLLM(
        [
            {
                "summary": "Read Tamil text aloud",
                "deliverables": [
                    {"id": "d1", "kind": "audio", "language_raw": "Tamil", "output_filename": "ta.mp3", "constraints": {"format": "mp3"}}
                ],
                "inputs_used": [str(txt.resolve())],
            },
            {
                "steps": [
                    {"id": "s1", "tool": "synthesize_speech", "args": {"path": str(txt.resolve()), "language": "ta", "format": "mp3"}, "deliverable_id": "d1"},
                    {"id": "v1", "tool": "verify_speech", "args": {"audio_path": "$s1.audio_path", "language": "ta", "chunk_manifest_path": "$s1.chunk_manifest_path"}, "deliverable_id": "d1"},
                    {"id": "pub1", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$s1.audio_path", "filename": "ta.mp3"}},
                ],
                "final_answer_template": "Done.",
            },
            # Epoch C round 1: LLM repeats identical synthesize_speech/verify_speech args without passing `attempt`.
            {
                "steps": [
                    {"id": "r1_s1", "tool": "synthesize_speech", "args": {"path": str(txt.resolve()), "language": "ta", "format": "mp3"}, "deliverable_id": "d1"},
                    {"id": "r1_v1", "tool": "verify_speech", "args": {"audio_path": "$r1_s1.audio_path", "language": "ta", "chunk_manifest_path": "$r1_s1.chunk_manifest_path"}, "deliverable_id": "d1"},
                    {"id": "r1_pub1", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$r1_s1.audio_path", "filename": "ta.mp3"}},
                ],
                "final_answer_template": "Recovered.",
            },
        ]
    )

    out_dir = tmp_path / "out_bug1"
    report = await run_agent(
        RunConfig(task="Read this Tamil text aloud", input_paths=[txt], out_dir=out_dir),
        llm=llm,
        bridge=bridge,
    )
    assert report.status == "success"
    assert all(v.passed for v in report.verification)
    # Initial synthesis hit /v1/audio/speech for 2 chunks; Epoch C should ONLY re-synthesize failed chunk 1 (total 3 calls, not 2 and not 4)!
    speak_http_calls = [c for c in st.calls if c["path"] == "/v1/audio/speech"]
    assert len(speak_http_calls) == 3


async def test_bug1_synthesize_speech_attempt_and_only_chunks_directly(tmp_path: Path) -> None:
    """Direct tool check: attempt=0 caches; attempt=1 + only_chunks=[1] re-samples only chunk 1 and re-stitches."""
    st = FakeBodhanState(speak_duration_s=2.0, speak_distinct_per_call=True)
    tk, _ = _toolkit(tmp_path, st)
    long_text = (
        "வணக்கம் முதல் பகுதி இது " + ("தமிழ் " * 30) + "முடிந்தது.\n\n"
        + "இரண்டாவது பகுதி இது " + ("பாடம் " * 30) + "முடிந்தது."
    )
    r0 = await tk.synthesize_speech("ta", text=long_text, format="mp3", output_dir=str(tmp_path / "o"))
    assert r0["ok"] is True
    assert r0["data"]["chunk_count"] == 2
    assert len(st.speak_inputs_seen) == 2

    # Same call at attempt=0 must hit cache (0 new HTTP calls — "never send same input twice" rule intact).
    r0_cached = await tk.synthesize_speech("ta", text=long_text, format="mp3", output_dir=str(tmp_path / "o"))
    assert r0_cached["ok"] is True
    assert r0_cached["cache_hit"] is True
    assert len(st.speak_inputs_seen) == 2

    # Retry only chunk 1 with attempt=1: chunk 0 reused from cache, ONLY chunk 1 hits API!
    r1 = await tk.synthesize_speech(
        "ta",
        text=long_text,
        format="mp3",
        attempt=1,
        only_chunks=[1],
        output_dir=str(tmp_path / "o"),
    )
    assert r1["ok"] is True
    assert r1["cache_hit"] is False
    assert r1["data"]["chunk_count"] == 2
    assert len(st.speak_inputs_seen) == 3
    assert Path(r1["data"]["audio_path"]).is_file()


# ---------------------------------------------------------------------------
# BUG 2: Markdown tables & syntax converted to speakable text before TTS
# ---------------------------------------------------------------------------
async def test_bug2_markdown_table_converted_to_speakable_text_and_verified(tmp_path: Path) -> None:
    """Markdown tables, separator rows, headings, bold, and inline pipes must never be sent literally to TTS."""
    md = (
        "# **பள்ளி அறிவியல்** ஆய்வகப் பட்டியல் (2026)\n\n"
        "| பொருள் | அளவு | விலை |\n"
        "| --- | --- | --- |\n"
        "| கண்ணாடி பீக்கர் 250 மிலி | 24 | 1800 |\n"
        "| டிஜிட்டல் தெர்மோமீட்டர் | 12 | 3600 |\n\n"
        "மொத்த பொருட்கள்: 36 | மொத்த விலை: 5400"
    )
    st = FakeBodhanState(speak_duration_s=5.0)
    tk, _ = _toolkit(tmp_path, st)
    res = await tk.synthesize_speech("ta", text=md, gender="male", format="mp3", output_dir=str(tmp_path / "o"))
    assert res["ok"] is True

    spoken_blob = "\n".join(st.speak_inputs_seen)
    # Zero literal Markdown table pipes, separator dashes, headings, or bold markers sent to TTS!
    assert "|" not in spoken_blob
    assert "---" not in spoken_blob
    assert "#" not in spoken_blob
    assert "**" not in spoken_blob
    # Table rows converted into labeled sentences using the original Bodhan header labels:
    assert "பொருள்: கண்ணாடி பீக்கர் 250 மிலி; அளவு: 24; விலை: 1800." in spoken_blob
    assert "பொருள்: டிஜிட்டல் தெர்மோமீட்டர்; அளவு: 12; விலை: 3600." in spoken_blob

    # Chunk manifest stores the speakable text and verify_speech compares against it.
    manifest = json.loads(Path(res["data"]["chunk_manifest_path"]).read_text(encoding="utf-8"))
    assert all("speakable_text" in entry for entry in manifest)
    st.transcribe_texts = [entry["speakable_text"] for entry in manifest]
    ver = await tk.verify_speech(
        res["data"]["audio_path"],
        "ta",
        chunk_manifest_path=res["data"]["chunk_manifest_path"],
    )
    assert ver["ok"] is True
    assert ver["data"]["passed"] is True
    assert ver["data"]["mean_cer"] == 0.0

    # Provenance check must pass on the speakable text (all Indic words are byte-identical substrings of Bodhan output).
    store = ArtifactStore(out_dir=tmp_path / "prov_out", allowed_input_paths=[])
    orig_md = store.work_dir / "translated_ta.md"
    orig_md.write_text(md, encoding="utf-8")
    store.register_step_output(
        "t1",
        "translate_text",
        {"ok": True, "model": "indic-translate", "cost_inr": 0.01, "data": {"text_path": str(orig_md), "target_language": "ta"}},
    )
    speakable_file = store.work_dir / "speakable.txt"
    speakable_file.write_text(spoken_blob, encoding="utf-8")
    store.provenance_by_path[str(speakable_file.resolve())] = store.provenance_by_path[str(orig_md.resolve())].model_copy(
        update={"path": str(speakable_file.resolve())}
    )
    store.assert_indic_provenance(speakable_file)


# ---------------------------------------------------------------------------
# BUG 3: indic-translate Indic->Indic returning English -> two-hop Bodhan pivot
# ---------------------------------------------------------------------------
async def test_bug3_translate_text_pivots_via_english_and_guards_wrong_script(tmp_path: Path) -> None:
    """When Indic->Indic returns English, translate_text pivots through English via Bodhan and records pivot_used."""
    bn_text = "প্রতি শীতকালে পরিযায়ী পাখিরা পূর্ব ভারতের শান্ত জলাভূমিতে বিশ্রাম নিতে আসে।"

    # Case 1: Bengali -> Tamil returns English on hop 1, then Tamil on hop 2 (English -> Tamil).
    st1 = FakeBodhanState(translate_indic_to_indic_returns_english=True)
    tk1, _ = _toolkit(tmp_path / "case1", st1)
    res1 = await tk1.translate_text("ta", text=bn_text, output_dir=str(tmp_path / "case1" / "out"))
    assert res1["ok"] is True
    assert res1["data"]["pivot_used"] is True
    out1 = Path(res1["data"]["text_path"]).read_text(encoding="utf-8")
    assert script_ratio_for_language(out1, "ta") >= 0.8

    # Case 2: Model persistently returns English even after pivot -> structured wrong_output_script error.
    st2 = FakeBodhanState(translate_always_english=True)
    tk2, _ = _toolkit(tmp_path / "case2", st2)
    res2 = await tk2.translate_text("ta", text=bn_text, output_dir=str(tmp_path / "case2" / "out"))
    assert res2["ok"] is False
    assert res2["error"]["code"] == "wrong_output_script"
    assert res2["error"]["retryable"] is False
    assert res2["error"]["hint"]

    # Case 3: Target 'en' returns Indic script -> structured wrong_output_script error.
    st3 = FakeBodhanState(translate_map={"en": "এটি ইংরেজি নয়, এটি বাংলা লেখা।"})
    tk3, _ = _toolkit(tmp_path / "case3", st3)
    res3 = await tk3.translate_text("en", text=bn_text, output_dir=str(tmp_path / "case3" / "out"))
    assert res3["ok"] is False
    assert res3["error"]["code"] == "wrong_output_script"
    assert res3["error"]["retryable"] is False
    assert res3["error"]["hint"]


# ---------------------------------------------------------------------------
# BUG 4: Reused --out directory clears stale agent outputs, preserves other files
# ---------------------------------------------------------------------------
async def test_bug4_reused_out_dir_clears_only_agent_outputs(tmp_path: Path) -> None:
    """Running into an existing --out folder clears stale agent outputs without deleting unrelated files."""
    bn_src = tmp_path / "bn.txt"
    bn_text = "এটি একটি বাংলা পরীক্ষা বাক্য।"
    bn_src.write_text(bn_text, encoding="utf-8")

    out_dir = tmp_path / "reused_out"
    (out_dir / "artifacts").mkdir(parents=True)
    (out_dir / ".work").mkdir(parents=True)
    (out_dir / "user_folder").mkdir(parents=True)

    # Stale agent outputs from an earlier run:
    stale_mp3 = out_dir / "artifacts" / "old_stale_audio.mp3"
    stale_mp3.write_bytes(b"STALE_MP3")
    stale_work = out_dir / ".work" / "old_work.tmp"
    stale_work.write_text("stale", encoding="utf-8")
    (out_dir / "trace.jsonl").write_text('{"stale": true}\n', encoding="utf-8")
    (out_dir / "report.json").write_text('{"stale": true}', encoding="utf-8")
    (out_dir / "result.md").write_text("stale result", encoding="utf-8")
    (out_dir / "trace.html").write_text("<html>stale</html>", encoding="utf-8")

    # Unrelated user files inside --out that MUST NOT be deleted:
    keep_file = out_dir / "user_notes.txt"
    keep_file.write_text("keep this file", encoding="utf-8")
    keep_nested = out_dir / "user_folder" / "keep.dat"
    keep_nested.write_text("keep nested", encoding="utf-8")

    st = FakeBodhanState(transcribe_by_lang={"bn": bn_text}, speak_duration_s=2.0)
    tk, _ = _toolkit(tmp_path, st)
    bridge = InProcessMCPBridge(tk)
    llm = ScriptedFakeLLM(
        [
            {
                "summary": "Bengali TTS",
                "deliverables": [
                    {"id": "d1", "kind": "audio", "language_raw": "Bengali", "output_filename": "fresh.mp3", "constraints": {"voice_gender": "female", "format": "mp3"}}
                ],
                "inputs_used": [str(bn_src.resolve())],
            },
            {
                "steps": [
                    {"id": "s1", "tool": "synthesize_speech", "args": {"path": str(bn_src.resolve()), "language": "bn", "gender": "female", "format": "mp3"}, "deliverable_id": "d1"},
                    {"id": "s2", "tool": "verify_speech", "args": {"audio_path": "$s1.audio_path", "language": "bn", "chunk_manifest_path": "$s1.chunk_manifest_path"}, "deliverable_id": "d1"},
                    {"id": "s3", "tool": "publish_artifact", "args": {"deliverable_id": "d1", "source_ref": "$s1.audio_path", "filename": "fresh.mp3"}},
                ],
                "final_answer_template": "Fresh Bengali audio created.",
            },
        ]
    )

    report = await run_agent(
        RunConfig(task="Read this paragraph aloud in Bengali with a female voice", input_paths=[bn_src], out_dir=out_dir),
        llm=llm,
        bridge=bridge,
    )
    assert report.status == "success"
    # Stale outputs cleared:
    assert not stale_mp3.exists()
    assert not stale_work.exists()
    assert sorted(p.name for p in (out_dir / "artifacts").iterdir()) == ["fresh.mp3"]
    # Unrelated files untouched:
    assert keep_file.read_text(encoding="utf-8") == "keep this file"
    assert keep_nested.read_text(encoding="utf-8") == "keep nested"


def test_compute_cer_spoken_tolerates_spelled_out_numbers_while_rejecting_errors() -> None:
    exp = "பொருள்: கண்ணாடி பீக்கர் 250 மிலி; அளவு: 24; விலை: 1800."
    # Spelled-out ASR transcription (without ITN) should score low CER:
    hyp_spoken = "பொருள் கண்ணாடி பீக்கர் டூ ஹண்ட்ரட் அண்ட் ஃபிஃப்டி மிலி அளவு டுவெண்ட்டி ஃபோர் விலை எயிட்டீன் ஹண்ட்ரட்"
    assert compute_cer_spoken(exp, hyp_spoken) < 0.15
    # Exact digit match should be 0.0:
    assert compute_cer_spoken(exp, exp) == 0.0
    # Wrong digits must be penalized:
    assert compute_cer_spoken("அளவு 24 விலை 1800", "அளவு 99 விலை 7777") > 0.3
    # Wrong language / truncated speech must still fail badly (well above 0.45 threshold):
    assert compute_cer_spoken(exp, "Item Glass Beaker Quantity 24 Cost 1800") > 0.7
    assert compute_cer_spoken(exp, "பொருள் கண்ணாடி") > 0.5

