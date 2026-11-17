import json

import pytest

from bodhan_agent.requirements import requirements, validate
from conftest import input_ref, wav_bytes


def test_generic_output_duration_and_format():
    assert requirements(
        "Make an approximately two-minute Tamil MP3 audio summary of this chapter."
    ) == {"audio_required": True, "audio_seconds": [90, 150], "audio_format": ".mp3"}
    assert requirements("Create a 45-second spoken WAV explanation from these notes.") == {
        "audio_required": True,
        "audio_seconds": [33.75, 56.25],
        "audio_format": ".wav",
    }
    assert requirements("Transcribe this three-minute voice note into subtitles.") == {
        "subtitles_required": True
    }
    assert requirements("Translate this video into French.") == {"translation_required": True}


def test_translation_request_cannot_finish_with_only_planner_prose(run):
    ref = run.save_text("An invented English translation.", "list", "planner_english")
    errors = validate("Translate these clips into English and save a list.", run, [ref])
    assert any("Bodhan translation" in error for error in errors)


@pytest.mark.parametrize("task", [
    "Do not translate the recordings; identify their scripts only.",
    "Summarize this English explanation about translation.",
])
def test_negated_translation_or_source_description_does_not_require_translation(task):
    assert not requirements(task).get("translation_required")


@pytest.mark.parametrize("task", [
    "Transcribe this Hindi note and give me English subtitles covering the whole recording.",
    "Create SRT captions for this audio recording.",
    "Write English subtitles using this video recording.",
    "Give subtitles about this recording.",
])
def test_subtitle_source_recording_is_not_an_output_audio_requirement(task):
    result = requirements(task)
    assert result["subtitles_required"] is True
    assert not result.get("audio_required")


def test_measurement_rejects_wrong_duration_and_format(run, monkeypatch):
    from bodhan_agent import requirements as module

    ref = input_ref(run, name="short.wav")
    run.checks.append({"kind": "audio", "ref": ref, "checked": True, "passed": True})
    monkeypatch.setattr(module, "duration", lambda p, timeout: 83.592)
    errors = validate("Make a two-minute MP3 audio summary of this text.", run, [ref])
    assert len(errors) == 2 and "missing" in errors[0]
    monkeypatch.setattr(module, "duration", lambda p, timeout: 120)
    assert not validate("Make a two-minute WAV audio summary of this text.", run, [ref])


async def test_false_completed_audio_gets_bounded_autonomous_feedback(run, fake, monkeypatch):
    import httpx
    from bodhan_agent.runner import execute
    from bodhan_core.gateway import Gateway
    from test_agent import tool_response
    from bodhan_agent import requirements as module

    source = input_ref(run)
    output = run.artifacts / "audio.wav"
    output.write_bytes(b"not-decoded-in-mock")
    ref = run.register(output, "test_only")
    monkeypatch.setattr(module, "duration", lambda p, timeout: 83.592)
    seen = []

    def planner(request):
        import json

        payload = json.loads(request.content)
        seen.append(payload["messages"])
        return tool_response(
            "finish",
            {"status": "completed", "outputs": [ref], "explanation": "Done.", "assumptions": []},
            len(seen),
        )

    fake.planner = planner
    report = await execute(
        "Make a two-minute WAV audio summary of these notes.",
        run,
        Gateway(run, httpx.MockTransport(fake)),
    )
    assert report["status"] == "partial"
    assert len(seen) == 3
    assert any("requested_output_mismatch" in m.get("content", "") for m in seen[1])
    assert source in run.refs


async def test_auth_rejection_stops_without_language_probing(run):
    import httpx
    from bodhan_agent.runner import execute
    from bodhan_core.gateway import Gateway
    from test_agent import tool_response

    source = input_ref(run)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "mock-planner.example":
            return httpx.Response(
                200, json=tool_response("translate", {"source": source, "target_language": "ta"}, 1)
            )
        return httpx.Response(403, json={"error": {"message": "Invalid credentials"}})

    report = await execute("Translate into Tamil.", run, Gateway(run, httpx.MockTransport(handler)))
    assert report["status"] == "failed"
    assert report["explanation"].startswith("authentication_rejected")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "task",
    [
        "Read this paragraph aloud in Bengali with a female voice.",
        "Please narrate these notes in Tamil.",
        "Translate this paragraph and then read it aloud.",
        "Create an audio explanation from this document.",
        "Give me an MP3 from these notes.",
    ],
)
async def test_requested_audio_cannot_finish_with_only_text(tools, run, fake, task):
    from bodhan_agent.runner import execute
    from test_agent import tool_response

    input_ref(run)
    artifact = run.save_text("Only an English explanation.", "english", "planner_english")
    turns = []

    def planner(request):
        turns.append(json.loads(request.content)["messages"])
        return tool_response(
            "finish",
            {"status": "completed", "outputs": [artifact], "explanation": "Done.", "assumptions": []},
            len(turns),
        )

    fake.planner = planner
    report = await execute(task, run, tools.gateway)
    assert report["status"] == "partial"
    assert len(turns) == 3
    assert "audio" in report["explanation"].lower()


def test_selected_audio_needs_its_own_explicit_passing_check(run):
    output = run.artifacts / "narration.wav"
    output.write_bytes(wav_bytes())
    ref = run.register(output, "bodhan_ai_generated")
    run.checks.append({"kind": "audio", "ref": "another_ref", "checked": True, "passed": True})
    assert validate("Read this aloud.", run, [ref])
    run.checks.append({"kind": "audio", "ref": ref, "checked": False, "passed": True})
    assert validate("Read this aloud.", run, [ref])
    run.checks[-1]["checked"] = True
    assert not validate("Read this aloud.", run, [ref])


async def test_selected_translation_needs_verification_even_without_task_keyword(tools, run):
    result = await tools.call(
        "translate", {"source": input_ref(run), "target_language": "ta"}
    )
    assert result["ok"]
    ref = result["ref"]
    checks = list(run.checks)
    run.checks.clear()
    assert validate("Process this document.", run, [ref])
    run.checks.extend(checks)
    assert not validate("Process this document.", run, [ref])


def test_subtitles_request_cannot_finish_with_markdown(run):
    ref = run.save_text("Only a transcript.", "english", "planner_english")
    assert validate("Transcribe this three-minute note into English subtitles.", run, [ref])
    output = run.artifacts / "subtitles.srt"
    output.write_text("1\n00:00:00,000 --> 00:00:01,000\nA subtitle.\n")
    srt = run.register(output, "bodhan_translation")
    run.checks.append({"kind": "translation", "ref": srt, "checked": True, "passed": True})
    assert not validate("Transcribe this three-minute note into English subtitles.", run, [srt])


def test_input_audio_does_not_require_output_audio(run):
    ref = run.save_text("A text summary.", "english", "planner_english")
    assert not validate("Summarize this two-minute audio recording in English text.", run, [ref])
    assert not validate("Make a written summary from this audio recording.", run, [ref])
    assert not validate("Summarize notes about people who speak Bengali.", run, [ref])
    assert not validate("Do not narrate; return a written summary.", run, [ref])


def test_requested_format_and_duration_must_match_the_same_audio(run, monkeypatch):
    from bodhan_agent import requirements as module

    selected = []
    for filename in ("short.mp3", "long.wav"):
        path = run.artifacts / filename
        path.write_bytes(wav_bytes())
        ref = run.register(path, "bodhan_ai_generated")
        selected.append(ref)
        run.checks.append({"kind": "audio", "ref": ref, "checked": True, "passed": True})
    monkeypatch.setattr(module, "duration", lambda p, timeout: 10 if p.suffix == ".mp3" else 120)
    assert validate("Create two-minute MP3 audio.", run, selected)


async def test_planner_can_correct_missing_audio_after_finish_feedback(tools, run, fake):
    from bodhan_agent.runner import execute
    from test_agent import tool_response

    source = input_ref(run)
    text = run.save_text("Only text.", "english", "planner_english")
    turns = 0

    def planner(request):
        nonlocal turns
        turns += 1
        if turns == 1:
            return tool_response(
                "finish",
                {"status": "completed", "outputs": [text], "explanation": "Done.", "assumptions": []},
                turns,
            )
        data = json.loads(json.loads(request.content)["messages"][-1]["content"].split("\n", 1)[-1])
        if turns == 2:
            assert data["error"]["code"] == "requested_output_mismatch"
            return tool_response("synthesize", {"source": source, "language": "en"}, turns)
        return tool_response(
            "finish",
            {"status": "completed", "outputs": [data["ref"]], "explanation": "Verified audio.", "assumptions": []},
            turns,
        )

    fake.planner = planner
    report = await execute("Read the paragraph aloud.", run, tools.gateway)
    assert report["status"] == "completed" and turns == 3
    assert any(check["kind"] == "audio" and check["passed"] for check in report["verification"])
