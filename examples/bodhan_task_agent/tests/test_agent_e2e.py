"""End-to-end: the real agent loop + MCP client + server + tools, with a fake Bodhan API and a scripted LLM.

The scripted LLM is a *policy*: it reads the tool results the agent feeds back (paths, errors)
and decides the next call, exactly as a real model would have to.
"""

import json
from pathlib import Path

from bodhan_agent.agent import Agent
from bodhan_agent.budget import Budget
from bodhan_agent.llm import LLMResponse, ScriptedLLM, ToolCall
from bodhan_agent.trace import load, replay_responses
from bodhan_mcp.server import build_server
from fakes import tone_wav

_n = 0


def call(name: str, **args) -> ToolCall:
    global _n
    _n += 1
    return ToolCall(id=f"c{_n}", name=name, arguments=json.dumps(args, ensure_ascii=False))


def say(thought: str, *calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=thought, tool_calls=list(calls), usage={"prompt_tokens": 10, "completion_tokens": 5})


def last_results(messages: list[dict]) -> list[dict]:
    """Tool results of the most recent turn, parsed."""
    out = []
    for m in reversed(messages):
        if m["role"] == "tool":
            out.insert(0, json.loads(m["content"].split(' …"[truncated')[0]))
        elif m["role"] == "assistant":
            break
    return out


class Steps:
    """Turn a list of functions (messages -> LLMResponse) into a policy."""

    def __init__(self, *fns):
        self.fns = list(fns)
        self.i = 0

    def __call__(self, messages, tools):
        fn = self.fns[min(self.i, len(self.fns) - 1)]
        self.i += 1
        return fn(messages)


def plan(feasible=True, reason=None, **kw):
    return lambda m: say("Planning.", call("submit_plan", understanding="do the task", feasible=feasible,
                                           infeasible_reason=reason, steps=["..."], **kw))


async def run(tmp_path, service, policy, task, files, **budget):
    out = tmp_path / "out"
    agent = Agent(ScriptedLLM(policy=policy), budget=Budget(**budget), server=build_server(service=service))
    state = await agent.run(task, files, out)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return state, report, out


async def test_multistep_hindi_voice_note_to_english_subtitles(tmp_path, service, fake):
    note = tmp_path / "inputs" / "voice_note.wav"
    note.parent.mkdir()
    note.write_bytes(tone_wav(45.0, sr=16000, bursts=[(i * 4.5, i * 4.5 + 3.6) for i in range(10)]))
    policy = Steps(
        plan(source_languages=["hi"], target_languages=["en"], assumptions=["the note is in Hindi, as stated"]),
        lambda m: say("Transcribe the Hindi note.", call("transcribe_audio", path=str(note), language="hi")),
        lambda m: say("Translate the timed segments.", call("translate_text", target_language="en",
                                                            text_file=last_results(m)[0]["result"]["segments_file"])),
        lambda m: say("Write the SRT.", call("make_subtitles", segments_file=last_results(m)[0]["result"]["segments_file"])),
        lambda m: say("Done.", call("finish", status="completed", answer="English subtitles for the Hindi voice note.",
                                    artifacts=[{"path": last_results(m)[0]["result"]["subtitle_file"],
                                                "description": "English subtitles (SRT)"}])),
    )
    _, rep, out = await run(tmp_path, service, policy, "Transcribe this Hindi voice note and give me English subtitles",
                                [str(note.parent)])
    assert rep["status"] == "completed", rep
    srt = out / rep["artifacts"][0]["file"]
    assert srt.suffix == ".srt" and "-->" in srt.read_text(encoding="utf-8")
    assert rep["verification"]["passed"] and rep["cost_inr"]["total"] > 0
    assert (out / "result.md").read_text(encoding="utf-8").startswith("# Task result: completed")
    events = load(out / "trace.jsonl")
    tools = [e for e in events if e["type"] == "tool"]
    assert [e["tool"] for e in tools] == ["transcribe_audio", "translate_text", "make_subtitles"]
    for e in tools:  # every step line has the fields the brief asks for
        assert {"plan", "tool", "arguments", "result_summary", "latency_ms", "cost_inr"} <= set(e)
    assert events[-1]["type"] == "run_end" and any(e["type"] == "summary" for e in events)


async def test_read_aloud_translates_then_speaks_with_verification(tmp_path, service, fake):
    para = tmp_path / "para.txt"
    para.write_text("The library opens at 9 in the morning. Children can borrow two books each week.", encoding="utf-8")
    policy = Steps(
        plan(target_languages=["bn"]),
        lambda m: say("Translate to Bengali.", call("translate_text", target_language="bn", text_file=str(para))),
        lambda m: say("Speak it.", call("synthesize_speech", language="bn", gender="female",
                                        text_file=last_results(m)[0]["result"]["output_file"])),
        lambda m: say("Done.", call("finish", status="completed", answer="Bengali audio, female voice.",
                                    artifacts=[{"path": last_results(m)[0]["result"]["audio_file"], "description": "audio"}])),
    )
    _, rep, _ = await run(tmp_path, service, policy, "Read this paragraph aloud in Bengali with a female voice", [str(para)])
    assert rep["status"] == "completed", rep["verification"]
    assert rep["artifacts"][0]["ai_generated_audio"]
    # The Bengali text that was spoken ships next to the audio, without the LLM listing it.
    assert [a["file"].endswith(".bn.txt") for a in rep["artifacts"]] == [False, True]
    assert rep["artifacts"][1]["description"].startswith("Text spoken in")
    names = {c["name"] for c in rep["verification"]["checks"]}
    assert {"speech_back_transcription", "translation_checks", "audio_decodes"} <= names
    assert rep["cost_inr"]["by_model"]["indic-speak"] > 0


async def test_unsupported_language_fails_honestly_without_any_model_call(tmp_path, service, fake):
    video = tmp_path / "talk.wav"
    video.write_bytes(tone_wav(2.0))
    policy = Steps(plan(feasible=False, reason="French is not supported by Bodhan's translation or speech models."))
    _, rep, out = await run(tmp_path, service, policy, "Translate this video into French", [str(video)])
    assert rep["status"] == "infeasible" and rep["artifacts"] == []
    assert "French" in rep["answer"] and sum(fake.requests.values()) == 0
    assert not any((out / "artifacts").iterdir())
    assert rep["steps"]["used"] == 1


async def test_tiny_budget_stops_cleanly_and_reports(tmp_path, service, fake):
    txt = tmp_path / "t.txt"
    txt.write_text("নমস্কার। " * 200, encoding="utf-8")
    policy = Steps(
        plan(),
        lambda m: say("Speak.", call("synthesize_speech", language="bn", text_file=str(txt))),
        lambda m: say("Out of budget.", call("finish", status="failed", answer="The budget was too small to synthesise.")),
    )
    _, rep, _ = await run(tmp_path, service, policy, "Read this aloud in Bengali", [str(txt)], max_cost_inr=0.01)
    assert rep["status"] == "budget_exhausted"
    assert rep["cost_inr"]["total"] <= 0.01 and fake.requests["/v1/audio/speech"] == 0
    assert any(e.get("code") == "budget_exceeded" for e in rep["errors"])


async def test_guards_block_llm_authored_indic_text_and_foreign_paths(tmp_path, service, fake):
    policy = Steps(
        plan(),
        lambda m: say("Try to cheat.", call("write_text_file", filename="x.md", content="यह मैंने लिखा"),
                      call("synthesize_speech", language="hi", text="नमस्ते दोस्तों"),
                      call("read_text_file", path="/etc/passwd")),
        lambda m: say("Give up.", call("finish", status="failed", answer="Could not do it within the rules.")),
    )
    _, rep, _ = await run(tmp_path, service, policy, "Say hello in Hindi", [])
    codes = [e["code"] for e in rep["errors"]]
    assert codes.count("indic_text_from_llm") == 2 and "path_not_allowed" in codes
    assert fake.requests["/v1/audio/speech"] == 0 and rep["status"] == "failed"


async def test_false_success_claim_is_rejected_then_downgraded(tmp_path, service, fake):
    policy = Steps(
        plan(),
        lambda m: say("Claim success.", call("finish", status="completed", answer="Here is your audio.",
                                             artifacts=[{"path": str(tmp_path / "out" / "work" / "fake.mp3"),
                                                         "description": "audio"}])),
        lambda m: say("Claim again.", call("finish", status="completed", answer="Here is your audio.",
                                           artifacts=[{"path": str(tmp_path / "out" / "work" / "fake.mp3"),
                                                       "description": "audio"}])),
    )
    _, rep, _ = await run(tmp_path, service, policy, "Make Tamil audio", [])
    assert rep["agent_claimed_status"] == "completed" and rep["status"] != "completed"
    assert rep["artifacts"] == []


async def test_prompt_injection_in_ocr_is_flagged_and_not_followed(tmp_path, service, fake):
    from PIL import Image

    img = tmp_path / "worksheet.png"
    Image.new("RGB", (300, 300), "white").save(img)
    policy = Steps(
        plan(target_languages=["mr"]),
        lambda m: say("OCR it.", call("ocr_document", path=str(img))),
        lambda m: (say("Injection noted; translating to Marathi as the user asked.",
                       call("translate_text", target_language="mr",
                            text_file=last_results(m)[0]["result"]["markdown_file"]))
                   if "security_warning" in last_results(m)[0] else say("??", call("finish", status="failed", answer="x"))),
        lambda m: say("Done.", call("finish", status="completed",
                                    answer="Marathi worksheet; embedded instructions in the image were ignored.",
                                    artifacts=[{"path": last_results(m)[0]["result"]["output_file"], "description": "md"}])),
    )
    _, rep, out = await run(tmp_path, service, policy, "Translate this worksheet into Marathi and keep the table",
                            [str(img)])
    assert rep["status"] == "completed", rep
    assert rep["prompt_injection"] and "Untrusted content" in (out / "result.md").read_text(encoding="utf-8")
    md = (out / rep["artifacts"][0]["file"]).read_text(encoding="utf-8")
    assert md.count("|") >= 9  # the table survived


async def test_parallel_calls_split_budget_and_identical_calls_are_memoised(tmp_path, service, fake):
    clips = []
    for i in range(3):
        p = tmp_path / f"clip{i}.wav"
        p.write_bytes(tone_wav(3.0 + i, sr=16000))
        clips.append(p)
    policy = Steps(
        plan(),
        lambda m: say("All clips at once.", *[call("transcribe_audio", path=str(c)) for c in clips]),
        lambda m: say("Oops, again.", call("transcribe_audio", path=str(clips[0]))),
        lambda m: say("Done.", call("finish", status="completed", answer="None of the clips are in Telugu.")),
    )
    _, rep, out = await run(tmp_path, service, policy, "Which of these clips are in Telugu?", [str(c) for c in clips])
    tools = [e for e in load(out / "trace.jsonl") if e["type"] == "tool"]
    assert sum(1 for e in tools if e["step"] == 2) == 3
    assert tools[-1]["memoised"] and tools[-1]["cost_inr"] == 0
    assert rep["status"] == "completed"


async def test_replay_reproduces_the_run_from_the_trace(tmp_path, service, fake):
    para = tmp_path / "p.txt"
    para.write_text("Water boils at 100 degrees.", encoding="utf-8")
    policy = Steps(
        plan(),
        lambda m: say("Translate.", call("translate_text", target_language="ta", text_file=str(para))),
        lambda m: say("Done.", call("finish", status="completed", answer="Tamil text.",
                                    artifacts=[{"path": last_results(m)[0]["result"]["output_file"], "description": "t"}])),
    )
    _, rep1, out = await run(tmp_path, service, policy, "Translate this into Tamil", [str(para)])
    recorded = [LLMResponse.from_dict(r) for r in replay_responses(load(out / "trace.jsonl"), tmp_path / "replay")]
    before = sum(fake.requests.values())
    agent = Agent(ScriptedLLM(recorded), budget=Budget(), server=build_server(service=service))
    await agent.run("Translate this into Tamil", [str(para)], tmp_path / "replay")
    rep2 = json.loads((tmp_path / "replay" / "report.json").read_text(encoding="utf-8"))
    assert rep2["status"] == rep1["status"] == "completed"
    assert sum(fake.requests.values()) == before  # replay is free: everything comes from the cache
    assert Path(tmp_path / "replay" / rep2["artifacts"][0]["file"]).read_text(encoding="utf-8") == \
        Path(out / rep1["artifacts"][0]["file"]).read_text(encoding="utf-8")
