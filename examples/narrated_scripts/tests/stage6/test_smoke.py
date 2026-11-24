"""Synthetic hosted-mode smoke test with production media processing."""

import io
import json
import struct
import wave
from base64 import b64decode
from collections import Counter
from email import policy
from email.parser import BytesParser
from math import pi, sin

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.audio.export import ATTRIBUTION, CHAPTER_TOLERANCE_SECONDS, run_media
from backend.ocr.inputs import png_bytes
from backend.translation.segments import TOKEN
from bodhan_lesson import main
from tools.make_sample import make_sample

FOOTER = "Classroom geometry notes"
EQUATION = r"65^\circ + 115^\circ = 180^\circ"
TABLE = """<table>
<tr><th>Smaller angle</th><th>Larger angle</th></tr>
<tr><td>65°</td><td>115°</td></tr>
<tr><td>80°</td><td>100°</td></tr>
</table>"""

# The OCR records correspond to the two locally drawn pages.
PAGE_BLOCKS = (
    (
        ("title", "Angles on a straight line"),
        ("text", "A straight line makes an angle of 180 degrees."),
        ("text", "Place a point on the line and draw a ray from that point."),
        ("text", "The ray divides the straight angle into two parts."),
        ("text", "A classroom example"),
        ("image", ""),
        ("image-caption", "Figure 1. A ray divides a straight angle."),
        ("text", "If the smaller angle measures 65 degrees, then the"),
        ("footer", FOOTER),
        ("page-number", "1"),
    ),
    (
        ("text", "larger angle measures 115 degrees."),
        ("title", "Checking the sum"),
        ("equation", f"$$\n{EQUATION}\n$$"),
        ("table-caption", "Table 1. Two pairs of adjacent angles"),
        ("table", TABLE),
        ("text", "Try another pair whose sum is 180 degrees."),
        ("footer", FOOTER),
        ("page-number", "2"),
    ),
)

# Each translation preserves the protected placeholders in order.
TRANSLATIONS = {
    "Angles on a straight line": ("सीधी रेखा पर कोण", "সরলরেখার ওপর কোণ"),
    "A straight line makes an angle of {}.": ("सीधी रेखा {} का कोण बनाती है।", "একটি সরলরেখা {} কোণ তৈরি করে।"),
    "Place a point on the line and draw a ray from that point.": (
        "रेखा पर एक बिंदु रखें और उस बिंदु से एक किरण खींचें।", "রেখায় একটি বিন্দু রেখে সেখান থেকে একটি রশ্মি আঁকো।",
    ),
    "The ray divides the straight angle into two parts.": (
        "किरण सरल कोण को दो भागों में बाँटती है।", "রশ্মি সরলকোণকে দুই ভাগে ভাগ করে।",
    ),
    "A classroom example": ("कक्षा का एक उदाहरण", "শ্রেণিকক্ষের একটি উদাহরণ"),
    "{}. A ray divides a straight angle.": ("{}। एक किरण सरल कोण को बाँटती है।", "{}। একটি রশ্মি সরলকোণ ভাগ করে।"),
    "If the smaller angle measures {}, then the larger angle measures {}.": (
        "यदि छोटा कोण {} है, तो बड़ा कोण {} है।", "ছোট কোণটি {} হলে বড় কোণটি {} হয়।",
    ),
    "Checking the sum": ("योग की जाँच", "যোগফল যাচাই"),
    "{}. Two pairs of adjacent angles": ("{}। आसन्न कोणों के दो जोड़े", "{}। সন্নিহিত কোণের দুই জোড়া"),
    "Smaller angle": ("छोटा कोण", "ছোট কোণ"),
    "Larger angle": ("बड़ा कोण", "বড় কোণ"),
    "{}": ("{}", "{}"),
    "Try another pair whose sum is {}.": ("एक और जोड़ा खोजें जिसका योग {} हो।", "আরেকটি জোড়া খোঁজো যার যোগফল {}।"),
    "{} degrees plus {} degrees equals {} degrees.": (
        "{} डिग्री और {} डिग्री का योग {} डिग्री है।", "{} ডিগ্রি ও {} ডিগ্রি যোগ করলে {} ডিগ্রি হয়।",
    ),
}


def chat_response(text: str, **fields) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 8, "total_tokens": 16},
        **fields,
    })


def tone_wav(frequency: int) -> bytes:
    rate = 24_000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        samples = [round(6000 * sin(2 * pi * frequency * index / rate)) for index in range(rate // 2)]
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def install_provider_doubles(monkeypatch, scans, out) -> Counter:
    requests = Counter()
    clips = {}
    original_client_init = httpx.Client.__init__

    original_pages = {}
    for number in (1, 2):
        with Image.open(scans / f"page-{number}.png") as image:
            original_pages[png_bytes(image)] = number

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-smoke-only"
        if request.url.host == "smoke.invalid" and request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content)
            assert payload["model"] == "synthetic-smoke"
            instruction = payload["messages"][0]["content"]
            if instruction.startswith("Identify the main language"):
                requests["language"] += 1
                return chat_response("en")
            if instruction.startswith("Select one complete English sentence"):
                requests["figure"] += 1
                return chat_response("{}")
            pytest.fail(f"Unexpected synthetic LLM request: {instruction}")

        assert request.url.host == "api.bodhan.ai", f"Unexpected remote request: {request.url}"
        if request.url.path == "/v1/audio/transcriptions":
            requests["transcribe"] += 1
            headers = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode("ascii")
            message = BytesParser(policy=policy.default).parsebytes(headers + request.content)
            parts = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                     for part in message.iter_parts()}
            language, reference = clips[parts["file"]]
            assert parts["language"].decode() == language
            assert parts["model"] == b"indic-transcribe"
            return httpx.Response(200, json={"text": reference})

        payload = json.loads(request.content)
        model = payload["model"]
        if request.url.path == "/v1/chat/completions" and model == "indic-ocr":
            image_url = payload["messages"][0]["content"][0]["image_url"]["url"]
            pixels = b64decode(image_url.removeprefix("data:image/png;base64,"), validate=True)
            normalized_pages = {path.read_bytes(): int(path.stem[1:]) for path in (out / "work/images").glob("p*.png")}
            number = (original_pages | normalized_pages)[pixels]
            requests["ocr"] += 1
            if requests["ocr"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            blocks = PAGE_BLOCKS[number - 1]
            return chat_response("\n\n".join(source for _, source in blocks), blocks=[
                {"label": kind, "text": source, "order": index, "conf": 0.99}
                for index, (kind, source) in enumerate(blocks)
            ])
        if request.url.path == "/v1/chat/completions" and model == "indic-translate":
            source = payload["messages"][0]["content"]
            assert FOOTER not in source
            key = TOKEN.sub("{}", source)
            assert key in TRANSLATIONS, f"Unexpected synthetic translation input: {key!r}"
            index = {"hi": 0, "bn": 1}[payload["target_language_code"]]
            requests["translate"] += 1
            return chat_response(TRANSLATIONS[key][index].format(*TOKEN.findall(source)))
        if request.url.path == "/v1/audio/speech" and model == "indic-speak":
            text = payload["input"]
            assert FOOTER not in text
            language = json.loads(payload["instructions"])["lang"]
            audio = tone_wav(330 + len(clips))
            clips[audio] = (language, text)
            requests["speak"] += 1
            return httpx.Response(200, content=audio)
        pytest.fail(f"Unexpected remote request: {request.url} ({model})")

    transport = httpx.MockTransport(respond)

    def offline_client(self, **kwargs):
        assert "transport" not in kwargs
        kwargs.update(transport=transport, trust_env=False)
        original_client_init(self, **kwargs)

    def reject_unmocked(*args, **kwargs):
        pytest.fail("The offline smoke test attempted to use a real model or network transport.")

    monkeypatch.setattr(httpx.Client, "__init__", offline_client)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_unmocked)
    for local in (runner.LocalOCR, runner.LocalTranslate, runner.LocalSpeak, runner.LocalTranscribe):
        monkeypatch.setattr(local, "__call__", reject_unmocked)
    return requests


def test_offline_smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("BODHAN_SPEECH_MAX_CHARS", "BODHAN_SPEECH_CER_THRESHOLD", "BODHAN_TRANSLATE_GGUF_URL"):
        monkeypatch.delenv(key, raising=False)
    for key in ("BODHAN_OCR_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY"):
        monkeypatch.setenv(key, "synthetic-smoke-only")
    monkeypatch.setenv("BODHAN_BACKEND", "hosted")
    monkeypatch.setenv("LLM_BASE_URL", "https://smoke.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "synthetic-smoke")
    scans, out = tmp_path / "scans", tmp_path / "synthetic-output"
    make_sample(scans)
    requests = install_provider_doubles(monkeypatch, scans, out)

    assert main(["--input", str(scans), "--langs", "hi,bn", "--out", str(out)]) == 0
    assert {"language", "figure", "ocr", "translate", "speak", "transcribe"} <= requests.keys()
    assert requests["speak"] == requests["transcribe"]

    def read_json(relative_path):
        return json.loads((out / relative_path).read_text(encoding="utf-8"))

    assert read_json("source/language.json")["code"] == "en"
    boundaries = read_json("work/boundaries.json")
    [join] = [decision for decision in boundaries if decision["action"] == "join_prose"]
    assert join["page_ids"] == ["p001", "p002"]
    for page_id in join["page_ids"]:
        assert (out / f"work/images/{page_id}.png").is_file()
        assert read_json(f"source/pages/{page_id}.json")["status"] == "ok"

    report = read_json("report.json")
    assert report["status"] in {"completed", "completed_with_misses"}
    assert report["backend"]["requested_mode"] == "hosted"
    assert report["backend"]["local_attempts"] == 0
    assert report["backend"]["hosted_indic_attempts"] > 0
    assert [language["language"] for language in report["languages"]] == ["hi", "bn"]
    assert any(attempt["model"] == "synthetic-smoke" for attempt in report["attempts"])
    assert "synthetic-smoke-only" not in (out / "report.json").read_text(encoding="utf-8")
    events = [json.loads(line) for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_finished"
    language_attempts = [event for event in events if event["event"] == "attempt"
                         and event["stage"] == "language" and event["entity_ids"] == ["p001"]]
    assert [(event["attempt_number"], event["error_category"]) for event in language_attempts] == [
        (1, "rate_limit"), (2, None),
    ]
    assert any(event["event"] == "script_chunk" and event["section_id"]
               and set(join["block_ids"]) <= set(event["source_block_ids"]) for event in events)
    assert all("synthetic-smoke-only" not in json.dumps(event) for event in events)

    source = (out / "source/lesson.src.md").read_text(encoding="utf-8")
    assert "then the larger angle measures 115 degrees." in source
    markdown_files = ["source/lesson.src.md"]
    for language in report["languages"]:
        code = language["language"]
        markdown_files.append(f"{code}/lesson.{code}.md")
        assert language["status"] in {"completed", "completed_with_misses"}
        assert language["section_coverage"] == {"narrated": 2, "total": 2}
        assert language["speech_qa"]["metrics"]["cer"] == 0
        assert read_json(f"{code}/translation-qa.json")["status"] == "completed"
        translated = (out / f"{code}/lesson.{code}.md").read_text(encoding="utf-8")
        translation_index = {"hi": 0, "bn": 1}[code]
        title = TRANSLATIONS["Angles on a straight line"][translation_index]
        assert f"# {title}" in translated

        script = read_json(f"{code}/narration_script.{code}.json")
        assert any(set(join["block_ids"]) <= set(chunk["source_block_ids"])
                   for section in script["sections"] for chunk in section["chunks"])
        assert any(chunk["policy"] == "figure_caption"
                   for section in script["sections"] for chunk in section["chunks"])
        assert all((out / section["audio_path"]).is_file() for section in script["sections"])
        markers = script["audio"]["chapters"]
        probe = run_media(["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json",
                           str(out / language["lesson_audio_path"])])
        audio = json.loads(probe.stdout)
        assert audio["format"]["format_name"] == "mp3"
        assert audio["format"]["tags"]["comment"] == ATTRIBUTION
        assert len(audio["chapters"]) == len(markers) == 2
        previous_start = -1.0
        for actual, marker in zip(audio["chapters"], markers, strict=True):
            assert actual["tags"]["title"] == marker["title"]
            start = float(actual["start_time"])
            assert previous_start < start < float(actual["end_time"])
            assert start == pytest.approx(marker["start_ms"] / 1000, abs=CHAPTER_TOLERANCE_SECONDS)
            previous_start = start

    for filename in markdown_files:
        text = (out / filename).read_text(encoding="utf-8")
        assert FOOTER not in text
        assert sum(line.startswith("# ") for line in text.splitlines()) == 2
        assert EQUATION in text
        assert "| 65° | 115° |\n| 80° | 100° |" in text
        assert "Figure 1" in text and "Table 1" in text
