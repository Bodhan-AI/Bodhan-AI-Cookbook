"""Unit tests for the ₹0 Feasibility Gate, Plan Compiler, Scope Lock, DAG waves, and Provenance Guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_agent.compiler import PlanCompileError, compile_plan
from bodhan_agent.gate import check_feasibility, pre_llm_feasibility_check
from bodhan_agent.models import (
    DeliverableConstraints,
    DeliverableSpec,
    ForeachSpec,
    PlanStep,
    ProbeFact,
    RawPlan,
    TaskSpec,
)
from bodhan_agent.quarantined import q_tool_schemas
from bodhan_agent.store import ArtifactStore, ProvenanceViolationError
from bodhan_mcp.schemas import FileFact
from bodhan_mcp.server import build_mcp_server


async def _full_catalog() -> list[dict]:
    mcp = build_mcp_server()
    tools = await mcp.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
        }
        for t in tools
    ] + q_tool_schemas()


def test_pre_llm_gate_rejects_french_and_missing_files_at_zero_cost(tmp_path: Path) -> None:
    vid = FileFact(path=str(tmp_path / "clip.mp4"), exists=True, kind="video", duration_s=45.0)
    res_fr = pre_llm_feasibility_check("Translate this video into French", [vid], max_cost_inr=20.0)
    assert res_fr.feasible is False
    assert res_fr.cost_floor_inr == 0.0
    assert "French" in (res_fr.reason or "")

    missing = FileFact(path=str(tmp_path / "nope.pdf"), exists=False, kind="missing")
    res_miss = pre_llm_feasibility_check("Summarize this PDF in Tamil", [missing], max_cost_inr=20.0)
    assert res_miss.feasible is False
    assert "missing" in (res_miss.reason or "").lower()


def test_feasibility_gate_rejects_budget_below_floor_and_unsupported_model_lang(tmp_path: Path) -> None:
    # 8-page PDF OCR floor = 8 * 0.20 = ₹1.60. Budget ₹0.50 -> rejected at ₹0!
    pdf_fact = FileFact(path=str(tmp_path / "ch.pdf"), exists=True, kind="pdf", page_count=8)
    spec = TaskSpec(
        summary="Tamil audio summary",
        deliverables=[
            DeliverableSpec(id="d1", kind="audio", language_raw="Tamil", language="ta", constraints=DeliverableConstraints(duration_s=120))
        ],
        inputs_used=[pdf_fact.path],
    )
    gate = check_feasibility(spec, [pdf_fact], max_cost_inr=0.50)
    assert gate.feasible is False
    assert gate.cost_floor_inr >= 1.60
    assert "Budget too small" in (gate.reason or "")

    # Bhojpuri is supported by indic-transcribe, NOT by indic-speak -> rejected at ₹0!
    txt_fact = FileFact(path=str(tmp_path / "t.txt"), exists=True, kind="text", char_count=100)
    spec_bho = TaskSpec(
        summary="Speak Bhojpuri",
        deliverables=[DeliverableSpec(id="d1", kind="audio", language_raw="Bhojpuri")],
        inputs_used=[txt_fact.path],
    )
    gate_bho = check_feasibility(spec_bho, [txt_fact], max_cost_inr=20.0)
    assert gate_bho.feasible is False
    assert "indic-speak" in (gate_bho.reason or "")


async def test_compiler_enforces_scope_lock_and_rejects_planner_indic_text(tmp_path: Path) -> None:
    catalog = await _full_catalog()
    inp = tmp_path / "doc.txt"
    inp.write_text("Hello world", encoding="utf-8")
    fact = FileFact(path=str(inp), exists=True, kind="text", char_count=11)
    spec = TaskSpec(
        deliverables=[DeliverableSpec(id="d1", kind="text", language="ta", language_raw="Tamil")],
        inputs_used=[str(inp)],
    )

    # 1. Path traversal / /etc/passwd rejected.
    bad_path_plan = RawPlan(
        steps=[
            PlanStep(id="s1", tool="translate_text", args={"path": "/etc/passwd", "target_language": "ta"}),
            PlanStep(id="s2", tool="publish_artifact", args={"deliverable_id": "d1", "source_ref": "$s1.text_path", "filename": "../../etc/evil.txt"}),
        ]
    )
    with pytest.raises(PlanCompileError) as ei:
        compile_plan(bad_path_plan, task_spec=spec, file_facts=[fact], probe_facts=[], tool_catalog=catalog, allowed_inputs={inp.resolve()})
    err_blob = " ".join(ei.value.errors)
    assert "outside allowed --files" in err_blob
    assert "plain basename" in err_blob

    # 2. Planner-authored Indic text literal rejected!
    indic_literal_plan = RawPlan(
        steps=[
            PlanStep(id="s1", tool="synthesize_speech", args={"text": "வணக்கம் உலகம்", "language": "ta"}),
        ]
    )
    with pytest.raises(PlanCompileError) as ei2:
        compile_plan(indic_literal_plan, task_spec=spec, file_facts=[fact], probe_facts=[], tool_catalog=catalog, allowed_inputs={inp.resolve()})
    assert "planner-authored Indic" in " ".join(ei2.value.errors)

    # 3. Out-of-scope target language rejected!
    wrong_lang_plan = RawPlan(
        steps=[
            PlanStep(id="s1", tool="translate_text", args={"path": str(inp), "target_language": "bn"}),
        ]
    )
    with pytest.raises(PlanCompileError) as ei3:
        compile_plan(wrong_lang_plan, task_spec=spec, file_facts=[fact], probe_facts=[], tool_catalog=catalog, allowed_inputs={inp.resolve()})
    assert "outside TaskSpec scope" in " ".join(ei3.value.errors)


async def test_compiler_infers_parallel_waves_foreach_and_auto_verify_speech(tmp_path: Path) -> None:
    catalog = await _full_catalog()
    c1 = tmp_path / "c1.wav"
    c2 = tmp_path / "c2.wav"
    c3 = tmp_path / "c3.wav"
    for c in (c1, c2, c3):
        c.write_bytes(b"RIFF")

    facts = [FileFact(path=str(c), exists=True, kind="audio", duration_s=10.0) for c in (c1, c2, c3)]
    probes = [
        ProbeFact(path=str(c1), language="te", confidence=0.95),
        ProbeFact(path=str(c2), language="hi", confidence=0.92),
        ProbeFact(path=str(c3), language="te", confidence=0.96),
    ]
    spec = TaskSpec(
        deliverables=[
            DeliverableSpec(id="d1", kind="markdown", language="en", constraints=DeliverableConstraints(filter_language="te"))
        ]
    )
    raw = RawPlan(
        steps=[
            PlanStep(
                id="tr",
                foreach=ForeachSpec(over="probe_facts", filter={"language": "te"}, as_var="clip"),
                tool="transcribe_audio",
                args={"path": "$clip.path", "language": "te"},
            ),
            PlanStep(
                id="tl",
                foreach=ForeachSpec(over="probe_facts", filter={"language": "te"}, as_var="clip"),
                tool="translate_text",
                args={"path": "$tr.text_path", "target_language": "en"},
            ),
            PlanStep(
                id="merge",
                tool="merge_text_artifacts",
                args={"inputs": ["$tl.text_path"], "filename": "telugu_en.md"},
            ),
        ]
    )
    compiled = compile_plan(
        raw,
        task_spec=spec,
        file_facts=facts,
        probe_facts=probes,
        tool_catalog=catalog,
        allowed_inputs={c.resolve() for c in (c1, c2, c3)},
    )
    # Wave 0: tr_0 and tr_1 run in parallel! Wave 1: tl_0 and tl_1 run in parallel! Wave 2: merge.
    assert len(compiled.waves) == 3
    assert {s.id for s in compiled.waves[0]} == {"tr_0", "tr_1"}
    assert {s.id for s in compiled.waves[1]} == {"tl_0", "tl_1"}
    assert [s.id for s in compiled.waves[2]] == ["merge"]

    # Also verify auto-insertion of verify_speech when omitted.
    txt = tmp_path / "bn.txt"
    txt.write_text("এটি বাংলা।", encoding="utf-8")
    spec_tts = TaskSpec(deliverables=[DeliverableSpec(id="d1", kind="audio", language="bn")])
    raw_tts = RawPlan(
        steps=[PlanStep(id="sp1", tool="synthesize_speech", args={"path": str(txt), "language": "bn", "gender": "female"})]
    )
    comp_tts = compile_plan(
        raw_tts,
        task_spec=spec_tts,
        file_facts=[FileFact(path=str(txt), exists=True, kind="text", char_count=10)],
        probe_facts=[],
        tool_catalog=catalog,
        allowed_inputs={txt.resolve()},
    )
    assert "verify_sp1" in comp_tts.steps_by_id
    assert comp_tts.steps_by_id["verify_sp1"].tool == "verify_speech"


def test_provenance_guard_blocks_untrusted_indic_text(tmp_path: Path) -> None:
    store = ArtifactStore(out_dir=tmp_path / "out", allowed_input_paths=[])
    with pytest.raises(ProvenanceViolationError):
        store.write_trusted_text_artifact("यह LLM द्वारा लिखा गया पाठ है", "bad.txt", source="q_llm:en")

    rogue = tmp_path / "out" / ".work" / "rogue.txt"
    rogue.write_text("தமிழ் உரை", encoding="utf-8")
    with pytest.raises(ProvenanceViolationError):
        store.assert_indic_provenance(rogue)
