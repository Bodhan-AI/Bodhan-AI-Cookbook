"""Run the public CLI end to end on ten Indic rows against simulated Bodhan and judge endpoints.

The endpoints follow the hosted request and response contracts, so every stage runs: text
checks, back-translation, waveform checks, back-transcription and alignment, the LLM judge,
thresholds, reports, and a zero-request replay from the cache.
"""

import json
import math
import re
import sys
from array import array

import httpx
import pytest
from bodhan_eval import providers
from bodhan_eval.__main__ import main
from bodhan_eval.audio_checks import SAMPLE_RATE, pcm_wav

REPORTS = ("items.jsonl", "summary.json", "summary.md", "calibration.json")
KEEP_OPEN = "ढक्कन को 10 मिनट तक खुला रखें।"
BOILS = "நீர் 100 °C இல் கொதிக்கிறது."
LINES = "পৃষ্ঠায় দুটি রেখা আঁকুন।"


def tone(seconds: float) -> array:
    """A speech-level tone with no silence, noise or clipping."""
    return array("h", (round(8000 * math.sin(2 * math.pi * 220 * n / SAMPLE_RATE)) for n in range(round(seconds * SAMPLE_RATE))))


def dataset_rows() -> tuple[list[dict], dict[int, str]]:
    """Ten rows covering clean output and one defect per detector, plus clip transcripts by length."""
    silent_gap = tone(1.2) + array("h", [0]) * (3 * SAMPLE_RATE) + tone(1.2)
    clips = {
        "hi-clean": (tone(2.0), KEEP_OPEN),
        "hi-cut": (tone(1.0), "ढक्कन को 10"),
        "ta-clean": (tone(2.1), BOILS),
        "ta-repeat": (tone(4.3), f"{BOILS} {BOILS}"),
        "bn-gap": (silent_gap, LINES),
        "bn-clean": (tone(2.3), LINES),
    }
    open_lid = "Keep the lid open for 10 minutes."
    boil = "Water boils at 100 °C."
    draw = "Draw two lines on the page."
    rows = [
        {"id": "hi-clean", "lang": "hi", "source": open_lid, "translation": KEEP_OPEN},
        {"id": "hi-flip", "lang": "hi", "source": open_lid, "translation": "ढक्कन को 10 मिनट तक बंद रखें।"},
        {"id": "hi-number", "lang": "hi", "source": open_lid, "translation": "ढक्कन को 20 मिनट तक खुला रखें।"},
        {"id": "hi-cut", "lang": "hi", "source": open_lid, "translation": KEEP_OPEN},
        {"id": "ta-clean", "lang": "ta", "source": boil, "translation": BOILS},
        {"id": "ta-english", "lang": "ta", "source": boil, "translation": boil},
        {"id": "ta-repeat", "lang": "ta", "source": boil, "translation": BOILS},
        {"id": "bn-clean", "lang": "bn", "source": draw, "translation": LINES},
        {"id": "bn-gap", "lang": "bn", "source": draw, "translation": LINES},
        {"id": "bn-bold", "lang": "bn", "source": "Read the **bold** word.", "translation": "**গাঢ় শব্দটি পড়ুন।"},
    ]
    transcripts = {}
    for row in rows:
        if row["id"] in clips:
            samples, transcript = clips[row["id"]]
            row["audio"] = f"audio/{row['id']}.wav"
            row["_wav"] = pcm_wav(samples)
            transcripts[len(samples)] = transcript
    return rows, transcripts


def endpoints(transcripts: dict[int, str], seen: list[str]):
    """Answer like the hosted APIs: Chat Completions for translation and the judge, form upload for ASR."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "api.bodhan.ai" and path == "/v1/audio/transcriptions":
            seen.append("transcribe")
            wav = request.content[request.content.index(b"RIFF") :]
            samples = int.from_bytes(wav[40:44], "little") // 2
            return httpx.Response(200, json={"text": transcripts.get(samples, "")})
        body = json.loads(request.content)
        if request.url.host == "api.bodhan.ai":
            seen.append("translate")
            assert body["model"] == "indic-translate" and body["target_language_code"] == "en"
            usage = {"prompt_tokens": 10, "completion_tokens": 8}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "A back-translation."}}], "usage": usage})
        seen.append("judge")
        assert body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}
        payload = json.loads(body["messages"][1]["content"])
        findings = []
        for segment in payload["segments"]:
            match = re.search(r"\[(\d+)\]बंद", segment["words"])
            if segment["field"] == "translation" and match:
                word = int(match.group(1))
                findings.append(
                    {
                        "category": "mistranslation",
                        "severity": "major",
                        "segment": segment["id"],
                        "first_word": word,
                        "last_word": word,
                        "strength": "clear",
                        "reason": "बंद means closed, but the source says open.",
                    }
                )
        verdict = json.dumps({"findings": findings, "verify_speech": False}, ensure_ascii=False)
        # Reasoning models often wrap the verdict. The parser must cope.
        content = f"<think>compare clause by clause</think>\n{verdict}"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}})

    return handle


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Write the dataset and route the provider client to the simulated endpoints."""
    rows, transcripts = dataset_rows()
    (tmp_path / "audio").mkdir()
    for row in rows:
        if "_wav" in row:
            (tmp_path / row["audio"]).write_bytes(row.pop("_wav"))
    dataset = tmp_path / "data.jsonl"
    dataset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    for name in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://judge.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-judge")
    monkeypatch.chdir(tmp_path)
    seen: list[str] = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_: real_client(transport=httpx.MockTransport(endpoints(transcripts, seen))))

    def run(output: str) -> int:
        argv = ["bodhan_eval", "--dataset", str(dataset), "--langs", "hi,ta,bn", "--out", output, "--cache", "cache.sqlite", "--max-cost-inr", "1"]
        monkeypatch.setattr(sys, "argv", argv)
        return main()

    return tmp_path, run, seen


def test_ten_indic_rows_end_to_end_and_cached_replay(harness):
    """Each seeded defect is flagged by its detector, clean rows stay clean, and a replay is free."""
    root, run, seen = harness
    assert run("cold") == 0
    rows = {row["id"]: row for row in map(json.loads, (root / "cold" / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    flagged = {key: sorted({flag["category"] for flag in row["flags"]}) for key, row in rows.items()}
    assert flagged == {
        "hi-clean": [],
        "hi-flip": ["mistranslation"],
        "hi-number": ["number"],
        "hi-cut": ["speech_omission"],
        "ta-clean": [],
        "ta-english": ["wrong_language"],
        "ta-repeat": ["speech_repetition"],
        "bn-clean": [],
        "bn-gap": ["garbled_audio"],
        "bn-bold": ["format"],
    }
    flip = rows["hi-flip"]["flags"][0]
    assert rows["hi-flip"]["translation"][flip["span"]["start"] : flip["span"]["end"]] == "बंद" == flip["evidence"]
    assert all(not ("ऀ" <= char <= "෿") for char in flip["detail"])
    gap = rows["bn-gap"]["flags"][0]
    assert gap["span"]["field"] == "audio_ms" and 1100 <= gap["span"]["start"] <= 1300
    assert {row["status"] for row in rows.values()} == {"scored"}
    summary = json.loads((root / "cold" / "summary.json").read_text(encoding="utf-8"))
    assert summary["quality"]["judge_repeat"]["compared"] == 10
    assert summary["quality"]["judge_repeat"]["agreement"] == 1
    assert {"translate", "transcribe", "judge"} <= set(seen)

    dispatched = len(seen)
    assert run("replay") == 0
    assert len(seen) == dispatched, "a replay from the cache must not call any model"
    for name in REPORTS:
        assert (root / "cold" / name).read_bytes() == (root / "replay" / name).read_bytes()
    final = json.loads((root / "replay" / "run.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert final["requests"] == 0
