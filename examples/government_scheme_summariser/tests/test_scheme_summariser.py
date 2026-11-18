"""Offline tests: no API key or network needed. Run from the recipe folder with `pytest tests/`."""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheme_summariser as ss  # noqa: E402

OCR_MARKDOWN = """SYNTHETIC SAMPLE FOR THE BODHAN AI COOKBOOK. NOT A REAL GOVERNMENT SCHEME.

Government of Sampleland
Department of Agriculture and Farmers Welfare

# Gramin Solar Pump Sahayata Yojana 2026

Notification No. AGRI/SP/2026/114 Dated: 02 November 2026

## Objective

The scheme helps small and marginal farmers replace diesel irrigation pumps with solar pumps.

## Benefits

- A subsidy of 60% of the cost of a solar pump, paid directly into the farmer's bank account.
- Women farmers and SC/ST farmers receive an additional 10% subsidy.

| Pump capacity | Approved cost | Maximum subsidy |
|---|---|---|
| 3 HP | Rs. 1,50,000 | Rs. 90,000 |

## Eligibility

- The applicant must be a farmer resident of Sampleland and aged 18 years or above.
- Tenant farmers may apply with a registered lease agreement of at least 3 years.

**Who is not eligible**

- Income tax payers and serving or retired government employees.

DOCUMENTS REQUIRED

1. Aadhaar card of the applicant.
2. Bank passbook first page with IFSC code.

## How to apply

1. Visit the nearest Common Service Centre (CSC) or apply online at www.example.org/solar-pump.

## Important dates

- Last date to apply: 31 December 2026.

## Helpline

Toll free helpline 1800-000-1234. Email: solarpump-help@example.org.
"""


@pytest.fixture
def card() -> ss.SchemeCard:
    return ss.build_card(OCR_MARKDOWN)


def test_title_is_first_heading(card):
    assert card.title == "Gramin Solar Pump Sahayata Yojana 2026"


def test_every_field_found_from_mixed_heading_styles(card):
    # Markdown headings, **bold** lines and ALL CAPS lines are all treated as headings.
    assert all(card.fields[key] for key, _, _ in ss.FIELDS), {k: v for k, v in card.fields.items() if not v}
    assert card.fields["exclusions"] == ["Income tax payers and serving or retired government employees."]
    assert card.fields["documents"][0] == "Aadhaar card of the applicant."


def test_card_lines_are_verbatim(card):
    source = OCR_MARKDOWN.replace("**", "")
    for lines in card.fields.values():
        for line in lines:
            if " — " in line:  # table rows are re-joined cell by cell
                assert all(cell in source for cell in line.split(" — "))
            else:
                assert line in source, line


def test_table_rows_kept_and_separator_dropped(card):
    assert "3 HP — Rs. 1,50,000 — Rs. 90,000" in card.fields["benefits"]
    assert not any("---" in line for line in card.fields["benefits"])


def test_facts(card):
    assert "31 December 2026" in card.facts["dates"]
    assert "1800-000-1234" in card.facts["phones"]
    assert "www.example.org/solar-pump" in card.facts["links"]
    assert any("1,50,000" in a for a in card.facts["amounts"])


# Shape of real indic-ocr output: headings are plain lines in `content`, and SectionHeader blocks.
REAL_OCR = [
    {
        "markdown": "unused when blocks are present",
        "blocks": [
            {"order": 0, "type": "PageHeader", "text": ""},
            {"order": 1, "type": "PageNumber", "text": "SYNTHETIC SAMPLE. NOT A REAL GOVERNMENT SCHEME."},
            {"order": 2, "type": "PageNumber", "text": "Government of Sampleland\nDepartment of Agriculture"},
            {"order": 3, "type": "PageNumber", "text": "Gramin Solar Pump Sahayata Yojana 2026"},
            {"order": 5, "type": "SectionHeader", "text": "Objective"},
            {"order": 6, "type": "Text", "text": "The scheme helps small farmers switch to solar pumps."},
            {"order": 7, "type": "SectionHeader", "text": "Who is not eligible"},
            {"order": 8, "type": "Text", "text": "• Income tax payers."},
        ],
    },
    {
        "markdown": "",
        "blocks": [
            {"order": 1, "type": "PageNumber", "text": "SYNTHETIC SAMPLE. NOT A REAL GOVERNMENT SCHEME."},
            {"order": 2, "type": "SectionHeader", "text": "Documents required"},
            {"order": 3, "type": "Text", "text": "• Aadhaar card of the applicant.\n• One passport-size photograph."},
            {"order": 4, "type": "SectionHeader", "text": "Helpline"},
            {"order": 5, "type": "Text", "text": "Toll free helpline 1800-000-1234."},
            {"order": 6, "type": "PageFooter", "text": ""},
        ],
    },
]


def test_card_from_real_ocr_blocks():
    markdown = ss.ocr_to_markdown(REAL_OCR)
    assert markdown.count("NOT A REAL GOVERNMENT SCHEME") == 1  # repeated watermark kept once
    card = ss.build_card(markdown)
    assert card.title == "Gramin Solar Pump Sahayata Yojana 2026"
    assert card.fields["about"] == ["The scheme helps small farmers switch to solar pumps."]
    assert card.fields["exclusions"] == ["Income tax payers."]
    assert card.fields["documents"] == ["Aadhaar card of the applicant.", "One passport-size photograph."]
    assert card.fields["contact"] == ["Toll free helpline 1800-000-1234."]


def test_plain_line_headings_without_blocks():
    card = ss.build_card("Kisan Yojana\n\nEligibility\n\n• Farmers aged 18 or above.\n\nHow to apply\n\n1. Visit the CSC.\n")
    assert card.title == "Kisan Yojana"
    assert card.fields["eligibility"] == ["Farmers aged 18 or above."]
    assert card.fields["how_to_apply"] == ["Visit the CSC."]


def test_sentence_naming_a_section_is_not_a_heading():
    assert ss._heading_text("Apply online at the portal.") is None
    assert ss._heading_text("Documents required") == "Documents required"


def test_missing_section_is_reported_not_invented():
    card = ss.build_card("# Some Yojana\n\n## Benefits\n\n- Rs. 5,000 per year.\n")
    assert card.fields["documents"] == []
    assert ss.NOT_STATED in card.to_markdown()


def test_fallbacks_without_headings():
    card = ss.build_card("Scheme for weavers.\nApply before 15 March 2027 at the block office.\nCall 1800-111-2222 for help.\n")
    assert card.fields["deadlines"] == ["Apply before 15 March 2027 at the block office."]
    assert card.fields["contact"] == ["Call 1800-111-2222 for help."]


@pytest.mark.parametrize(
    ("question", "field"),
    [
        ("Which documents do I need?", "documents"),
        ("What is the last date to apply?", "deadlines"),
        ("How much money will I get?", "benefits"),
        ("Can a tenant farmer apply?", "eligibility"),
        ("What is the helpline number?", "contact"),
        ("Where do I apply online?", "how_to_apply"),
    ],
)
def test_questions_route_to_the_right_section(card, question, field):
    answer = ss.answer_question(card, question)
    assert answer.field == field
    assert answer.found
    assert all(any(line in v for v in card.fields.values()) for line in answer.source_lines)


def test_tenant_question_finds_the_tenant_line(card):
    answer = ss.answer_question(card, "Can a tenant farmer apply?")
    assert any("Tenant farmers" in line for line in answer.source_lines)


def test_tenant_question_returns_only_the_tenant_line(card):
    answer = ss.answer_question(card, "Can a tenant farmer apply?")
    assert answer.source_lines == ["Tenant farmers may apply with a registered lease agreement of at least 3 years."]


def test_strong_match_in_another_section_wins(card):
    answer = ss.answer_question(card, "I am a retired government employee, can I apply?")
    assert answer.field == "exclusions"
    assert answer.source_lines == ["Income tax payers and serving or retired government employees."]


def test_retired_question_ignores_weaker_matches_in_other_sections(card):
    # Live phrasing after translation; "apply" + "service" also match the CSC line, which must not be included.
    answer = ss.answer_question(card, "I am retired from government service, can I apply?")
    assert answer.source_lines == ["Income tax payers and serving or retired government employees."]


def test_unanswerable_question_says_so(card):
    assert not ss.answer_question(card, "Is there a quota for cricket players?").found


def test_route_uses_word_boundaries():
    assert ss.route_question("Show me the page image") is None  # 'age' inside 'page'/'image' must not route


def test_speech_chunks_respect_limit():
    text = "पहला वाक्य। " + "a" * 30 + ", " + "b" * 300 + ". Short one."
    chunks = ss.speech_chunks(text, max_chars=120)
    assert chunks[0].startswith("पहला वाक्य।")
    assert all(len(c) <= 121 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_speech_chunks_pack_short_sentences():
    assert ss.speech_chunks("एक। दो। तीन।", max_chars=220) == ["एक। दो। तीन।"]
    assert len(ss.speech_chunks("A short line. " * 40, max_chars=220)) == 3


OK_OCR = {"choices": [{"message": {"content": "x"}}]}


def test_key_budget_429_waits_a_window_and_lowers_the_pace(monkeypatch):
    slept = []
    monkeypatch.setattr(ss.time, "sleep", slept.append)
    over = {"error": {"code": "rate_limit_exceeded_error", "message": "over budget", "request_id": "r"}}
    responses = [FakeResponse(200, OK_OCR) for _ in range(5)] + [FakeResponse(429, over), FakeResponse(200, OK_OCR)]
    client = ss.Bodhan(api_key="test-key-placeholder", session=FakeSession(responses))
    for _ in range(6):
        client.ocr(b"\x89PNG")
    assert client._limits["indic-ocr"] == 4  # 6 sent in the window when the 429 came, minus a margin
    assert slept[0] == ss.Bodhan.WINDOW_S  # (mocked sleep doesn't advance time, so the throttle may add a wait)


def test_model_busy_429_backs_off_briefly_and_keeps_the_pace(monkeypatch):
    slept = []
    monkeypatch.setattr(ss.time, "sleep", slept.append)
    busy = FakeResponse(429, {"error": {"code": "429", "message": "Maximum rate limit reached for the model"}})
    client = ss.Bodhan(api_key="test-key-placeholder", session=FakeSession([busy, busy, FakeResponse(200, OK_OCR)]))
    client.ocr(b"\x89PNG")
    assert slept == [5.0, 15.0]
    assert "indic-ocr" not in client._limits


def test_budget_exceeded_stops_immediately(monkeypatch):
    slept = []
    monkeypatch.setattr(ss.time, "sleep", slept.append)
    over = {"error": {"message": "ExceededBudget: over budget. Spend=10.04, Budget=10.0", "type": "budget_exceeded", "code": "429"}}
    session = FakeSession([FakeResponse(429, over)])
    with pytest.raises(ss.BudgetExceeded, match="BODHAN_SPEAK_API_KEY"):
        ss.Bodhan(api_key="test-key-placeholder", session=session).speak("नमस्ते", "hi")
    assert len(session.requests) == 1 and slept == []


def test_model_busy_429_gives_up_after_bounded_retries(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    busy = {"detail": "Maximum rate limit reached for the model"}
    session = FakeSession([FakeResponse(429, busy) for _ in range(5)])
    with pytest.raises(ss.BodhanError, match="429"):
        ss.Bodhan(api_key="test-key-placeholder", session=session).ocr(b"\x89PNG")
    assert len(session.requests) == 5


def _wav(n_frames: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x01\x00" * n_frames)
    return buf.getvalue()


def test_join_wavs_adds_gaps():
    joined = ss.join_wavs([_wav(100), _wav(200)], gap_ms=10)
    with wave.open(io.BytesIO(joined)) as wf:
        assert wf.getnframes() == 100 + 240 + 200


class FakeBodhan(ss.Bodhan):
    """Records calls and returns predictable values instead of hitting the API."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def translate(self, text, target_language, script=None):
        self.calls.append(("translate", (text, target_language, script)))
        prefix = f"<roman> [{target_language}]" if script == "roman" else f"[{target_language}]"
        return "\n".join(f"{prefix} {line}" for line in text.split("\n"))

    def transliterate(self, text, language, script="roman"):
        self.calls.append(("transliterate", (text, language, script)))
        return f"<{script}> {text}"

    def speak(self, text, lang, voice=None, style=ss.SPEECH_STYLE):
        self.calls.append(("speak", (text, lang)))
        return _wav(10)

    def transcribe(self, audio, language=None, filename="question.wav"):
        self.calls.append(("transcribe", (language,)))
        return "मुझे कौन से दस्तावेज़ चाहिए?"


def test_localise_card_translates_every_line_and_romanises(card):
    fake = FakeBodhan()
    local = ss.localise_card(fake, card, "hi")
    assert local.fields["documents"][0] == "[hi] Aadhaar card of the applicant."
    assert local.labels["documents"] == "[hi] Documents needed"
    assert local.roman.fields["documents"][0] == "<roman> [hi] Aadhaar card of the applicant."
    whatsapp = local.to_whatsapp()
    assert whatsapp.startswith("*<roman> [hi] Gramin Solar Pump")
    assert "*<roman> [hi] Documents needed*" in whatsapp
    assert local.to_whatsapp(roman=False).startswith("*[hi] Gramin Solar Pump")


def test_localise_card_batches_one_call_per_section(card):
    fake = FakeBodhan()
    ss.localise_card(fake, card, "hi")
    sections_with_lines = sum(1 for v in card.fields.values() if v)
    assert len(fake.calls) == 2 * (1 + sections_with_lines)  # labels batch + sections, native and roman


def test_translate_lines_falls_back_when_line_count_drifts():
    fake = FakeBodhan()
    fake.translate = lambda text, lang, script=None: "merged into one line" if "\n" in text else f"[{lang}] {text}"
    assert ss.translate_lines(fake, ["a", "b"], "hi") == ["[hi] a", "[hi] b"]


def test_translate_lines_splits_in_halves_not_per_line():
    calls = []

    def translate(text, lang, script=None):
        calls.append(text)
        lines = text.split("\n")
        return "dropped" if "bad" in lines and len(lines) > 1 else "\n".join(f"[{lang}] {x}" for x in lines)

    fake = FakeBodhan()
    fake.translate = translate
    lines = ["a", "b", "c", "d", "e", "f", "g", "bad"]
    assert ss.translate_lines(fake, lines, "hi") == [f"[hi] {x}" for x in lines]
    assert len(calls) < len(lines) + 1  # whole batch, then halves, then only the half with the odd line


def test_long_translate_requests_get_an_output_budget():
    session = FakeSession([FakeResponse(200, {"translation": "x"}), FakeResponse(200, {"translation": "x"})])
    client = ss.Bodhan(api_key="test-key-placeholder", session=session)
    client.translate("short", "hi")
    client.translate("y" * 500, "hi")
    assert "max_output_tokens" not in session.requests[0][2]["json"]
    assert session.requests[1][2]["json"]["max_output_tokens"] == 1000


def test_throttle_waits_when_budget_is_spent(monkeypatch):
    slept = []
    monkeypatch.setattr(ss.time, "sleep", slept.append)
    client = ss.Bodhan(api_key="test-key-placeholder", session=FakeSession([]))
    client._limits["indic-translate"] = 2
    for _ in range(3):
        client._throttle("indic-translate")
    assert len(slept) == 1 and 59 < slept[0] <= 60.1


def test_localise_card_english_is_free(card):
    fake = FakeBodhan()
    assert ss.localise_card(fake, card, "en").fields == card.fields
    assert fake.calls == []


def test_spoken_question_round_trip(card, monkeypatch):
    fake = FakeBodhan()
    monkeypatch.setattr(fake, "translate", lambda text, lang, script=None: "Which documents do I need?" if lang == "en" else f"[{lang}] {text}")
    reply = ss.ask(fake, card, "hi", audio=_wav(10))
    assert reply.answer.field == "documents"
    assert reply.text.startswith("[hi] Documents needed: Aadhaar card")
    assert reply.audio


def test_typed_roman_question_is_transliterated(card):
    fake = FakeBodhan()
    ss.ask(fake, card, "hi", text="kaun se documents chahiye", speak=False)
    assert ("transliterate", ("kaun se documents chahiye", "hi", "native")) in fake.calls


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.ok = status < 400
        self.text = json.dumps(self._body)
        self.content = b""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0)


def test_client_backs_off_once_on_429(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    session = FakeSession([FakeResponse(429, headers={"retry-after": "1"}), FakeResponse(200, {"translation": "नमस्ते"})])
    client = ss.Bodhan(api_key="test-key-placeholder", session=session)
    assert client.translate("Hello", "hi") == "नमस्ते"
    assert len(session.requests) == 2
    method, url, kw = session.requests[0]
    assert url == "https://api.bodhan.ai/translate"
    assert kw["headers"]["Authorization"] == "Bearer test-key-placeholder"


def test_per_model_keys_fall_back_to_default(monkeypatch):
    for var in [*ss.MODEL_KEY_VARS.values(), "BODHAN_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BODHAN_API_KEY", "default-key-placeholder")
    monkeypatch.setenv("BODHAN_OCR_API_KEY", "ocr-key-placeholder")
    monkeypatch.setenv("BODHAN_SPEAK_API_KEY", "<key for indic-speak>")  # template value counts as unset
    session = FakeSession([FakeResponse(200, {"translation": "x"}), FakeResponse(200, {"choices": [{"message": {"content": ""}}]})])
    client = ss.Bodhan(session=session)
    client.translate("Hello", "hi")
    client.ocr(b"\x89PNG")
    assert session.requests[0][2]["headers"]["Authorization"] == "Bearer default-key-placeholder"
    assert session.requests[1][2]["headers"]["Authorization"] == "Bearer ocr-key-placeholder"
    assert client.keys["indic-speak"] == "default-key-placeholder"
    assert client.missing_keys() == []


def test_missing_key_is_reported_per_model(monkeypatch):
    for var in [*ss.MODEL_KEY_VARS.values(), "BODHAN_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "translate-key-placeholder")
    client = ss.Bodhan(session=FakeSession([]))
    assert "indic-ocr" in client.missing_keys() and "indic-translate" not in client.missing_keys()
    with pytest.raises(ss.BodhanError, match="BODHAN_OCR_API_KEY"):
        client.ocr(b"\x89PNG")


def test_translate_falls_back_to_chat_transport_on_404():
    chat = {"choices": [{"message": {"content": " नमस्ते "}, "finish_reason": "stop"}]}
    session = FakeSession([FakeResponse(404, {"detail": "Not Found"}), FakeResponse(200, chat), FakeResponse(200, chat)])
    client = ss.Bodhan(api_key="test-key-placeholder", session=session)
    assert client.translate("Hello", "hi", script="roman") == "नमस्ते"
    assert client.translate("Hello", "hi") == "नमस्ते"  # remembers: no second 404 round trip
    urls = [r[1] for r in session.requests]
    assert urls == ["https://api.bodhan.ai/translate"] + ["https://api.bodhan.ai/v1/chat/completions"] * 2
    sent = session.requests[1][2]["json"]
    assert sent == {"model": "indic-translate", "messages": [{"role": "user", "content": "Hello"}], "target_language": "hi", "script": "roman"}


def test_disk_cache_reuses_translations_and_speech(tmp_path):
    session = FakeSession([FakeResponse(200, {"translation": "नमस्ते"}), FakeResponse(200, {"translation": "namaste"})])
    client = ss.Bodhan(api_key="test-key-placeholder", session=session, cache_dir=tmp_path)
    assert client.translate("Hello", "hi") == "नमस्ते"
    assert client.translate("Hello", "hi") == "नमस्ते"  # from disk
    assert client.translate("Hello", "hi", script="roman") == "namaste"  # different request, new call
    assert len(session.requests) == 2
    fresh = ss.Bodhan(api_key="test-key-placeholder", session=FakeSession([]), cache_dir=tmp_path)
    assert fresh.translate("Hello", "hi") == "नमस्ते"  # survives a restart


def test_client_surfaces_bodhan_errors():
    err = {"error": {"message": "invalid key", "code": "unauthorized", "request_id": "req_1"}}
    client = ss.Bodhan(api_key="test-key-placeholder", session=FakeSession([FakeResponse(401, err)]))
    with pytest.raises(ss.BodhanError, match="401 unauthorized: invalid key"):
        client.translate("Hello", "hi")


def test_ocr_request_shape():
    body = {"choices": [{"message": {"content": "# Title"}}], "blocks": [{"order": 0}]}
    session = FakeSession([FakeResponse(200, body)])
    result = ss.Bodhan(api_key="test-key-placeholder", session=session).ocr(b"\x89PNG")
    sent = session.requests[0][2]["json"]
    assert sent["model"] == "indic-ocr"
    assert sent["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result == {"markdown": "# Title", "blocks": [{"order": 0}]}


def test_pdf_and_image_to_pages(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "Scheme notice")
    pdf = doc.tobytes()
    pages = ss.document_to_pages(pdf, "notice.pdf", max_pages=2)
    assert len(pages) == 2 and pages[0][:4] == b"\x89PNG"
    png = doc[0].get_pixmap(dpi=300).tobytes("png")
    shrunk = ss.document_to_pages(png, "photo.png", max_side=1000)
    assert pymupdf.Pixmap(shrunk[0]).width <= 1000
