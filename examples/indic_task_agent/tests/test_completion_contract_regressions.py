"""The 20 November 2026 audit: "completed" was checked against the planner's own (mutable) plan, not the task.
(1) A plan that never declared a requested output, and (2) a revised plan that silently erased one, both
finished "completed". Now a revised plan must keep or explicitly drop earlier required outputs, and before
"completed" a fresh-context call reads the original task against the delivered items. That call is the same
model cross-checking itself (scripted here), so these tests prove the gate's wiring, not its judgement."""

import pytest
from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, last_ok, read_outputs

from bodhan_agent.cli import run
from bodhan_agent.llm import LLMError, PlannerLLM

TASK = "Translate the whole source into Tamil and provide both a Tamil Markdown file and MP3."


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


def source(tmp_path):
    src = tmp_path / "source.txt"
    src.write_text("The library opens on Monday at nine.", encoding="utf-8")
    return src


def finish(status="completed", answer="Completed the requested Tamil translation and audio."):
    return lambda m: [("finish", {"status": status, "final_answer": answer, "deliverables": [last_ok(m, "path")]})]


async def go(tmp_path, task, files, turns, coverage=None, **llm):
    chat = ScriptedChat(turns, coverage=coverage)
    report = await run(args(tmp_path, task, files), transport=FakeBodhan().transport,
                       llm=PlannerLLM(client=chat, model="scripted", **llm))
    return report, read_outputs(tmp_path), chat


ENGLISH_ONLY = [("record_plan", {"steps": ["write a note"], "deliverables": [{"kind": "text", "language": "en"}]})]
NOTE = [("write_text", {"name": "note.md", "content": "An unrelated English note."})]
NOT_COVERED = [{"request": "a Tamil Markdown file", "covered_by": None}, {"request": "MP3", "covered_by": None}]


async def test_an_output_the_plan_never_declared_blocks_completed(tmp_path):
    report, (_, rep, trace, _), chat = await go(tmp_path, TASK, [source(tmp_path)], [ENGLISH_ONLY, NOTE, finish(), finish(), finish()],
                                                coverage=[NOT_COVERED] * 3)
    assert report["status"] == rep["status"] == "partial"
    assert any("no matching delivered item" in x and "MP3" in x for x in rep["limitations"])
    assert [e["missing"] for e in trace if e["type"] == "coverage_check"][0] == ["a Tamil Markdown file", "MP3"]
    assert TASK in chat.coverage_seen[0][-1]["content"]  # the original task, not the plan, is what is checked
    assert all("record_plan" not in str(m) for m in chat.coverage_seen[0])  # fresh context: no plan, no conversation
    assert rep["deliverables"] and rep["deliverables"][0]["path"].endswith("note.md")  # usable output is kept


async def test_a_revised_plan_cannot_silently_erase_an_earlier_requested_output(tmp_path):
    tamil = [("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "translation", "language": "ta"},
                                                                                 {"kind": "audio", "language": "ta"}]})]
    report, (_, rep, trace, _), _ = await go(tmp_path, TASK, [source(tmp_path)], [tamil, ENGLISH_ONLY, NOTE, finish(), finish(), finish()])
    rejected = [e for e in trace if e["type"] == "control_rejected" and e["tool"] == "record_plan"]
    assert rejected and "translation (ta); audio (ta)" in rejected[0]["error"]
    assert [e["version"] for e in trace if e["type"] == "plan"] == [1]  # the erasing plan was not recorded
    assert report["status"] == rep["status"] == "partial"


async def test_a_dropped_output_the_task_still_requires_ends_partial_not_completed_with_warnings(tmp_path):
    tamil = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"},
                                                                        {"kind": "audio", "language": "ta"}]})]
    drop = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"}],
                             "dropped": [{"kind": "audio", "language": "ta", "reason": "audio is not needed"}]})]
    translate = lambda m: [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]  # noqa: E731
    missing_mp3 = lambda m: [{"request": "a Tamil Markdown file", "covered_by": [ln for ln in m[-1]["content"].splitlines()  # noqa: E731
                                                                                   if ln.startswith("- source_ta")][0][2:].split(":")[0]},
                             {"request": "MP3", "covered_by": None}]
    report, (_, rep, trace, _), _ = await go(tmp_path, TASK, [source(tmp_path)], [tamil, drop, translate, finish(), finish(), finish()],
                                             coverage=[missing_mp3] * 3)
    assert [e["version"] for e in trace if e["type"] == "plan"] == [1, 2]
    assert report["status"] == "partial"
    assert any(x.startswith("plan change: audio (ta)") and "audio is not needed" in x for x in rep["limitations"])


async def test_a_superseded_planning_mistake_may_be_dropped_when_the_task_check_agrees(tmp_path):
    task = "Translate the whole source into Tamil."
    wrong = [("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "translation", "language": "ta"},
                                                                                {"kind": "audio", "language": "ta"}]})]
    fixed = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"}],
                              "dropped": [{"kind": "audio", "language": "ta", "reason": "the task asks for a translation only"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]
    report, (_, rep, _, _), chat = await go(tmp_path, task, [source(tmp_path)], [wrong, fixed, translate, finish(answer="Translated it.")])
    assert report["status"] == "completed_with_warnings"  # completed, with the plan change reported
    assert any(x.startswith("plan change: audio (ta)") for x in rep["limitations"]) and len(chat.coverage_seen) == 1


async def test_an_answer_only_task_completes_when_the_answer_covers_it(tmp_path):
    src = source(tmp_path)
    plan = [("record_plan", {"steps": ["inspect_files"], "deliverables": [{"kind": "answer", "description": "which file is English"}]})]
    inspect = [("inspect_files", {"paths": [str(src)]})]
    done = [("finish", {"status": "completed", "final_answer": "source.txt is in English.", "deliverables": []})]
    report, _, chat = await go(tmp_path, "Which of these files is in English?", [src], [plan, inspect, done],
                               coverage=[[{"request": "which of these files is in English", "covered_by": "answer"}]])
    assert report["status"] == "completed" and len(chat.coverage_seen) == 1


async def test_mixed_feasible_and_impossible_task(tmp_path):
    task = "Translate the source into Tamil and into French."
    plan = [("record_plan", {"steps": ["translate to ta"], "deliverables": [{"kind": "translation", "language": "ta"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]
    # an honest partial needs no check
    report, _, chat = await go(tmp_path / "a", task, [source(tmp_path)], [plan, translate, finish("partial", "Tamil done; French is not supported.")])
    assert report["status"] == "partial" and chat.coverage_seen == []
    # claiming completed is refused when the task check finds French missing, and ends partial
    no_french = lambda m: [{"request": "into Tamil", "covered_by": "answer"}, {"request": "into French", "covered_by": None}]  # noqa: E731
    report, (_, rep, _, _), _ = await go(tmp_path / "b", task, [source(tmp_path)], [plan, translate, finish(), finish(), finish()],
                                         coverage=[no_french] * 3)
    assert report["status"] == "partial" and any("into French" in x for x in rep["limitations"])


@pytest.mark.parametrize("reply", ["I think it is all fine.",  # no tool call
                                   [],  # an empty list confirms nothing
                                   [{"request": "the Tamil file", "covered_by": "source_ta_final_v2.md"}],  # an item that was not delivered
                                   {"outputs": "everything"},  # malformed arguments
                                   LLMError("planner endpoint down")])  # the call itself fails
async def test_an_unconfirmed_coverage_check_never_permits_completed(tmp_path, reply):
    task = "Translate the whole source into Tamil."
    plan = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]
    report, (out, rep, trace, _), _ = await go(tmp_path, task, [source(tmp_path)], [plan, translate, finish(), finish(), finish()],
                                               coverage=[reply] * 3, max_attempts=1)
    assert report["status"] == rep["status"] == "partial"
    assert rep["deliverables"] and (out / rep["deliverables"][0]["path"]).is_file()  # the usable translation is still delivered
    checks = [e for e in trace if e["type"] == "coverage_check"]
    assert len(checks) == 3 and all(e["unavailable"] or e["missing"] for e in checks)


@pytest.mark.parametrize("covered_by", ["source_ta.md", "source_ta.md: translation, Tamil, made from source.txt", "`source_ta.md`",
                                        r"E:\out\artifacts\source_ta.md", ["source_ta.md", "answer"]])
async def test_the_ways_a_model_names_a_delivered_item_are_accepted(tmp_path, covered_by):
    # seen live: models echo the whole listed line rather than the name alone
    task = "Translate the whole source into Tamil."
    plan = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]
    reply = [{"request": "Translate the whole source into Tamil", "covered_by": covered_by, "note": "extra keys are ignored"}]
    report, _, _ = await go(tmp_path, task, [source(tmp_path)], [plan, translate, finish(answer="Done.\n- one line\n- another")], coverage=[reply])
    assert report["status"] == "completed"


async def test_a_plan_cannot_declare_an_output_bodhan_cannot_make_and_the_possible_part_is_delivered(tmp_path):
    # h13 (written by an independent reviewer): Bhojpuri is transcription-only, yet a plan with Bhojpuri audio was accepted
    # and the run gave up on the Malayalam audio it could make
    task = "Can you make this in Malayalam and in Bhojpuri?"
    both = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ml"},
                                                                      {"kind": "translation", "language": "bho"}]})]
    ml = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ml"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ml", "output_name": "source_ml"})]
    report, (_, _, trace, _), _ = await go(tmp_path, task, [source(tmp_path)],
                                           [both, ml, translate, finish("partial", "Made the Malayalam version; Bhojpuri translation is not available.")])
    rejected = [e["error"] for e in trace if e["type"] == "control_rejected"]
    assert rejected and "cannot produce translation in Bhojpuri (bho)" in rejected[0] and "deliver the rest" in rejected[0]
    assert report["status"] == "partial" and report["deliverables"][0]["language"] == "ml"


async def test_deliverable_paths_with_doubled_backslashes_are_found(tmp_path):
    task = "Translate the whole source into Tamil."
    plan = [("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "ta"}]})]
    translate = [("translate_text", {"path": str(tmp_path / "source.txt"), "target_language": "ta", "output_name": "source_ta"})]
    doubled = lambda m: [("finish", {"status": "completed", "final_answer": "Done.",  # noqa: E731
                                     "deliverables": [last_ok(m, "path").replace("\\", "\\\\")]})]  # Windows paths; a POSIX path is unchanged
    report, _, _ = await go(tmp_path, task, [source(tmp_path)], [plan, translate, doubled])
    assert report["status"] == "completed" and report["deliverables"][0]["path"].endswith("source_ta.md")


# --- GPT-6.1 Sol HIGH review of 14c2275..178afa2 (the completion contract above) ---------------------------------

def unit_agent(tmp_path, coverage=None, max_inr=20.0, client=None):
    from bodhan_agent.loop import Agent, Settings
    from bodhan_mcp.runtime import CostLedger, ResultCache, RunContext
    from bodhan_mcp.tools.common import ProvenanceGuard

    class Trace:
        def __init__(self):
            self.events = []

        def write(self, e):
            self.events.append(e)

    ctx = RunContext(tmp_path, CostLedger(max_inr=max_inr), ResultCache(None))
    ctx.provenance_guard = ProvenanceGuard()
    llm = PlannerLLM(client=client or ScriptedChat([], coverage=coverage), model="scripted", max_attempts=3)
    return Agent("Write an English note.", ctx, Trace(), Settings(), llm, [], [])


def record(agent, deliverables, dropped=None):
    import json

    from bodhan_agent.llm import ToolCall
    a = {"steps": ["write_text"], "deliverables": deliverables, **({"dropped": dropped} if dropped else {})}
    return agent._record_plan(ToolCall("p", "record_plan", a, json.dumps(a)))


async def test_coverage_retries_stay_inside_the_budget_and_failed_attempts_are_counted(tmp_path):
    from unittest.mock import patch
    attempts = 0
    chat = ScriptedChat([], coverage=[[{"request": "English note", "covered_by": "answer"}]])
    real = chat.create

    async def flaky(**kw):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("connection lost after the request was sent")
        return await real(**kw)
    chat.create = flaky
    a = unit_agent(tmp_path, max_inr=1.0, client=chat)
    a.settings.llm_input_inr_per_1m, a.settings.llm_output_inr_per_1m = 0.0, 800.0  # a coverage call may cost up to ₹0.80
    with patch("bodhan_agent.llm.retry_wait", return_value=0):
        gaps = await a._coverage_gaps([], "An English note.")
    assert attempts == 1  # a second ₹0.80 attempt does not fit in ₹1
    assert a.ctx.ledger.to_state()["llm_cost"] >= 0.8 and gaps and "could not run" in gaps[0]


async def test_a_blank_coverage_entry_and_an_empty_answer_certify_nothing(tmp_path):
    a = unit_agent(tmp_path, coverage=[[{"request": "   ", "covered_by": "answer"}]])
    gaps = await a._coverage_gaps([], " ")
    assert gaps and "listed no requested outputs" in gaps[0]
    a = unit_agent(tmp_path / "b", coverage=[[{"request": "a note", "covered_by": "answer"}]])
    gaps = await a._coverage_gaps([], " ")  # "answer" is not a delivered item when the answer is empty
    assert gaps and "no matching delivered item: a note" in gaps[0]


def test_one_revised_deliverable_cannot_stand_in_for_two_earlier_ones(tmp_path):
    a = unit_agent(tmp_path)
    two = [{"kind": "text", "language": "en", "description": "first file"}, {"kind": "text", "language": "en", "description": "second file"}]
    assert record(a, two)["ok"]
    r = record(a, two[:1])
    assert not r["ok"] and "leaves out required outputs" in r["error"]["message"] and len(a.plan["deliverables"]) == 2
    assert record(a, two[:1], dropped=[{"kind": "text", "language": "en", "reason": "the task asks for one file"}])["ok"]


def test_naming_the_language_of_an_earlier_language_neutral_output_is_not_a_drop(tmp_path):
    a = unit_agent(tmp_path)
    assert record(a, [{"kind": "text"}])["ok"] and record(a, [{"kind": "text", "language": "en"}])["ok"]


@pytest.mark.parametrize("name", ["- `note.md`", "- 'note.md'", "`- note.md`", "* note.md"])
def test_quoted_bullet_names_are_recognised_but_near_misses_are_not(name):
    from bodhan_agent.loop import _item_name
    assert _item_name(name) == "note.md"
    assert {_item_name(x) for x in ("note_final.md", "xnote.md", "note.md_extra")}.isdisjoint({"note.md"})
