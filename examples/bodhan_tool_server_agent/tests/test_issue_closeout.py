"""Tests for the issues that were first fixed only in part: hard plan-first rule, romanized-Hindi guard, per-chunk back-check,
quiet-point audio cuts, cache reuse, LLM retry, preflight limits and the small language and encoding fixes."""
import json

import httpx
import pytest
import respx
from conftest import make_wav, wav_bytes
from mock_server import Mock
from test_e2e import args, run, wire

from bodhan_agent import safety, verify
from bodhan_mcp import languages
from bodhan_mcp.tools import audio, speak

TA = "வணக்கம் நண்பர்களே இது ஒரு சிறிய சுருக்கம்"


# ---- #13 plan first, no exceptions ----
async def test_a_model_that_never_plans_runs_no_tool_and_fails(env, monkeypatch):
    m = Mock(lambda done, last, user: ("list_languages", {}))  # asks for a tool every turn, never submits a plan
    monkeypatch.setenv("BODHAN_BASE_URL", f"http://127.0.0.1:{m.port}")
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{m.port}/llm")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "fake")
    st = await run(args(env, "Translate this text into Tamil", []))
    rep = json.loads((env / "out" / "report.json").read_text())
    trace = [json.loads(x) for x in (env / "out" / "trace.jsonl").read_text().splitlines()]
    assert st == "failed" and rep["tool_calls_by_tool"] == {}
    assert any("never submitted a plan" in w for w in rep["warnings"])
    assert not any(x["type"] == "tool_call" and x.get("status") == "ok" for x in trace)


# ---- #7 romanized Hindi from the LLM ----
@pytest.mark.parametrize("text,expected", [
    ("aap kaise hain, mera naam hai Arjun", True),
    ("Namaste, aap kaise hain? Main theek hoon aur bahut accha hai", True),
    ("The water cycle moves water from the sea to clouds and back as rain.", False),
    ("Please translate the chapter into Tamil and keep the table.", False),
    ("The Hindi word hai means is.", False),  # one marker is not enough
])
def test_romanized_hindi_is_detected_but_plain_english_is_not(text, expected):
    assert safety.looks_romanized_indic(text) is expected
    assert safety.llm_wrote_indic({"text": text}) is expected


def test_romanized_text_is_removed_not_kept():
    assert safety.strip_indic("aap kaise hain, mera naam hai Arjun") == "[removed: non-English text]"


# ---- #10 per-chunk comparison ----
def test_one_lost_chunk_is_found_even_when_the_whole_text_scores_well():
    chunks = [TA, "இந்த பயன்பாடு தமிழில் பேசுகிறது நன்றி", "மழை பெய்யும் போது மேகங்கள் கனமாகின்றன"]
    back = " ".join([chunks[0], chunks[2]])  # chunk 1 was dropped by the speech engine
    assert verify.chunk_recall(chunks[1], back) < 0.6
    assert verify.chunk_recall(chunks[0], back) > 0.95 and verify.chunk_recall(chunks[2], back) > 0.95
    assert verify.similarity(" ".join(chunks), back) > 0.6  # the whole-text score alone would not have flagged it strongly


# ---- #20 quiet-point cuts ----
def _wav_with_quiet_gap(path, gap_at, secs=60, rate=16000):
    import array
    import math
    a = array.array("h", [int(8000 * math.sin(i / 20)) for i in range(int(secs * rate))])
    for i in range(int((gap_at - 0.2) * rate), int((gap_at + 0.2) * rate)):
        a[i] = 0
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(a.tobytes())
    return path


def test_without_any_silence_the_cut_goes_to_the_quietest_point(tmp_path):
    p = _wav_with_quiet_gap(tmp_path / "g.wav", gap_at=24.0)  # a short dip that silencedetect would miss (too short at 0.4 s? still quiet)
    cuts = audio.plan_cuts(60.0, [], quiet=lambda a, b: audio.quietest(p, a, b))
    assert abs(cuts[0][1] - 24.0) < 0.3 and all(b - a <= audio.MAX_CHUNK_S + 0.01 for a, b in cuts)


def test_uniform_loud_audio_still_gives_long_chunks(tmp_path):
    p = make_wav(tmp_path / "u.wav", 65, rate=16000)
    cuts = audio.plan_cuts(65.0, [], quiet=lambda a, b: audio.quietest(p, a, b))
    assert len(cuts) == 3 and cuts[0][1] > 16  # ties go to the later frame: no pile of 5 second chunks


# ---- #23 cache reuse and hint rules ----
@pytest.mark.asyncio
async def test_hinted_call_reuses_the_unhinted_transcript_of_the_same_audio(env):
    make_wav(env / "a.wav", 5, rate=16000)
    with respx.mock:
        route = respx.post("https://api.bodhan.ai/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "यह परीक्षण है"}))
        await audio.transcribe_audio(str(env / "a.wav"))
        await audio.transcribe_audio(str(env / "a.wav"), language="hi")
    assert route.call_count == 1


def _language_forms(route):
    return [b'name="language"' in call.request.content for call in route.calls]


@pytest.mark.asyncio
async def test_an_ambiguous_first_chunk_does_not_pass_its_guess_to_the_rest(env):
    make_wav(env / "long.wav", 65, rate=16000)
    with respx.mock:
        route = respx.post("https://api.bodhan.ai/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "यह परीक्षण है आणि नाही"}))  # Hindi and Marathi markers together
        await audio.transcribe_audio(str(env / "long.wav"))
    assert route.call_count == 3 and not any(_language_forms(route))


@pytest.mark.asyncio
async def test_a_sure_first_chunk_is_passed_on_as_a_hint(env):
    make_wav(env / "long.wav", 65, rate=16000)
    with respx.mock:
        route = respx.post("https://api.bodhan.ai/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "வணக்கம் நண்பர்களே"}))
        await audio.transcribe_audio(str(env / "long.wav"))
    flags = _language_forms(route)
    assert flags.count(False) == 1 and flags.count(True) == 2


# ---- speech cache and response validation ----
def test_valid_wav_check():
    assert speak._valid_wav(wav_bytes(1)) is True
    assert speak._valid_wav(b"not a wav") is False and speak._valid_wav(b"") is False


@pytest.mark.asyncio
async def test_damaged_speech_cache_entry_is_ignored(env):
    from bodhan_mcp import core
    text = "नमस्ते दोस्तों"
    voice = "Kavya"
    core.cache_put(speak._key(text, voice, "hi", None), b"garbage")
    with respx.mock:
        route = respx.post("https://api.bodhan.ai/v1/audio/speech").mock(return_value=httpx.Response(200, content=wav_bytes(1)))
        r = await speak.synthesize_speech(text=text, language="hi", voice=voice, format="wav", out_name="x.wav")
    assert route.call_count == 1 and r["duration_s"] > 0


@pytest.mark.asyncio
async def test_a_non_wav_speech_response_is_an_error_not_a_cached_file(env):
    from bodhan_mcp.core import ToolError
    with respx.mock:
        respx.post("https://api.bodhan.ai/v1/audio/speech").mock(return_value=httpx.Response(200, content=b"<html>oops</html>"))
        with pytest.raises(ToolError) as e:
            await speak.synthesize_speech(text="नमस्ते दोस्तों", language="hi", voice="Kavya", format="wav")
    assert e.value.code == "UPSTREAM_ERROR"


# ---- LLM retry ----
async def test_a_failing_llm_is_retried_before_the_run_gives_up(env, monkeypatch):
    seen = []

    def script(done, last, user):
        seen.append(1)
        return ("finish", {"status": "unsupported", "unsupported_language": "French", "reason_type": "language", "summary": "French."}) \
            if done else ("submit_plan", {"interpretation": "x", "steps": ["a"], "feasible": True})
    m = Mock(script)
    m.llm_fail = 3  # the OpenAI client retries twice on its own, so three failures also exhaust it
    wire(monkeypatch, m)
    monkeypatch.setattr("bodhan_agent.agent.asyncio.sleep", lambda _: _noop())
    await run(args(env, "Translate this into Tamil", []))
    trace = [json.loads(x) for x in (env / "out" / "trace.jsonl").read_text().splitlines()]
    errs = [x for x in trace if x["type"] == "llm_error"]
    assert errs and errs[0]["attempt"] == 1 and any(x["type"] == "plan" for x in trace)  # failed first, then went on to plan


async def _noop():
    return None


# ---- preflight limits ----
async def test_preflight_runs_in_parallel_and_stops_at_a_cap(env, monkeypatch):
    from bodhan_agent import agent
    (env / "many").mkdir()
    for i in range(agent.MAX_PREFLIGHT_FILES + 5):
        (env / "many" / f"f{i}.txt").write_text("x")
    live = {"now": 0, "max": 0}

    async def fake_call(sess, name, a, timeout):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        import asyncio
        fut = asyncio.get_running_loop().create_future()  # conftest patches asyncio.sleep, so wait on a timer instead
        asyncio.get_running_loop().call_later(0.001, fut.set_result, None)
        await fut
        live["now"] -= 1
        return {"path": a["path"], "kind": "text"}, None, 0.0
    monkeypatch.setattr(agent, "call", fake_call)
    facts, missing = await agent.preflight(None, [str(env / "many")], 5)
    assert len(facts) == agent.MAX_PREFLIGHT_FILES + 1 and facts[-1]["kind"] == "note" and "5 more files" in facts[-1]["error"]
    assert 1 < live["max"] <= 8 and missing == []


# ---- small language and encoding fixes ----
def test_language_codes_are_case_insensitive_and_variant_scripts_are_known():
    assert languages.norm("KS-DEVA") == "ks-Deva" and languages.norm("Hindi") == "hi" and languages.norm("sd-arab") == "sd-Arab"
    assert [verify.expected_script(c) for c in ("sd", "sd-Arab", "ks", "ks-Deva", "mni", "mni-Beng")] == \
        ["Devanagari", "Arabic", "Arabic", "Devanagari", "MeeteiMayek", "Bengali"]
    assert list(languages.script_counts("ꯃꯅꯤꯄꯨꯔꯤ")) == ["MeeteiMayek"] and languages.detect_language("ꯃꯅꯤꯄꯨꯔꯤ")["language"] == "mni"


def test_manipuri_in_meitei_or_bengali_script_passes_the_script_check():
    ok_meitei = verify.check_translation("hello world " * 30, "ꯃꯅꯤꯄꯨꯔꯤ " * 30, "mni", "plain", [])
    ok_bengali = verify.check_translation("hello world " * 30, "মণিপুরী " * 30, "mni", "plain", [])
    bad = verify.check_translation("hello world " * 30, "नमस्ते " * 30, "mni", "plain", [])
    assert ok_meitei["ok"] and ok_bengali["ok"] and not bad["ok"]


def test_text_encoding_is_reported_truthfully(env):
    (env / "u16.txt").write_bytes("नमस्ते दुनिया".encode("utf-16"))
    (env / "bom.txt").write_bytes(b"\xef\xbb\xbf" + "नमस्ते".encode())
    (env / "plain.txt").write_text("hello")
    assert [audio.inspect_media(str(env / n))["text_encoding"] for n in ("u16.txt", "bom.txt", "plain.txt")] == ["utf-16", "utf-8-sig", "utf-8"]


def test_unknown_extension_with_audio_inside_is_still_probed(env):
    p = env / "voice.xyz"
    p.write_bytes((env / "x.wav").read_bytes() if (env / "x.wav").exists() else make_wav(env / "x.wav", 1, rate=16000).read_bytes())
    info = audio.inspect_media(str(p))
    assert info["kind"] == "audio" and info["duration_s"] > 0


def test_scrubber_removes_any_home_folder_not_only_ours(tmp_path):
    from bodhan_agent.agent import scrubber
    scrub = scrubber(tmp_path / "out")
    text = '{"path": "/Users/someone_else/Desktop/x/chapter.pdf", "b": "/home/bob/f.wav", "c": "' + str(tmp_path / "out") + '/artifacts/a.srt"}'
    out = scrub(text)
    assert "/Users/" not in out and "/home/" not in out and "someone_else" not in out and "bob" not in out
    assert "~/Desktop/x/chapter.pdf" in out and '"artifacts/a.srt"' in out
