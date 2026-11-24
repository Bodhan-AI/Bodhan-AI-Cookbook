import json

import pytest
from PIL import Image

from backend import runner
from backend.audio.narration import build_script, split_for_speech
from backend.config import LANGUAGES
from backend.models import NarrationEntry, SourceChapter, SourceSection, SpokenNumber, TargetNarrationPlan
from backend.translation.provider import ModelTranslation, TranslationResponse
from backend.translation.segments import TOKEN, plan_units
from bodhan_lesson import main


def entry(text, *, section="s001", action="reuse_translation", policy="paragraph", **fields):
    block_id = "p001-b001" if section == "s001" else "p002-b001"
    return NarrationEntry(section, block_id, (block_id,), policy, action, (block_id,), text, **fields)


def script_for(entries, translate, *, sections=("s001",), max_chars=300, voice=None, target="hi"):
    chapter = SourceChapter("en", tuple(SourceSection(section, None, None, None, ()) for section in sections))
    narration = TargetNarrationPlan(target, "completed", tuple(entries))
    return build_script(chapter, narration, plan_units(chapter), translate, max_chars=max_chars, voice=voice)


def unexpected_translation(*args):
    pytest.fail(f"Unexpected Indic-Translate call for {args[0]}")


def test_checked_prose_is_reused_verbatim_with_its_provenance():
    translated = "  पहले पढ़ें।\nफिर $a/(b+c)$ देखें।  "
    script = script_for([entry(translated)], unexpected_translation)
    [section] = script.sections
    [chunk] = section.chunks
    assert chunk.text == translated
    assert chunk.id == "s001-c01" and chunk.section_id == "s001"
    assert chunk.source_block_ids == chunk.unit_ids == ("p001-b001",)
    assert not section.issues and section.reason is None


def test_tamil_script_localizes_reference_labels_and_inline_degrees():
    calls = []

    def translate(unit_id, source, target, payload):
        assert source == "en" and target == "ta"
        calls.append(payload)
        tokens = TOKEN.findall(payload)
        if payload == "figure":
            text = "படம்"
        elif payload.endswith(" degrees."):
            [number] = tokens
            text = f"{number} பாகை"
        else:
            pytest.fail(f"Unexpected math payload: {payload}")
        return TranslationResponse(text, (f"a{len(calls):06d}",))

    text = r"ஒரு **செங்கோணம்** $90^{\circ}$ ஆகும் (Fig. 6.2 ஐப் பார்க்கவும்). மீண்டும் $90^\circ$."
    [section] = script_for([entry(text)], translate, target="ta").sections
    [chunk] = section.chunks
    assert chunk.text == (
        "ஒரு செங்கோணம் தொண்ணூறு பாகை ஆகும் , படம் ஆறு புள்ளி இரண்டு ஐப் பார்க்கவும். "
        "மீண்டும் தொண்ணூறு பாகை."
    )
    assert len(calls) == 2
    assert chunk.source_block_ids == ("p001-b001",)
    assert chunk.translation_attempt_ids == ("a000001", "a000002")


@pytest.mark.parametrize(("text", "expected", "policy"), [
    ("6.1 அறிமுகம்", "ஆறு புள்ளி ஒன்று அறிமுகம்", "heading"),
    ("6.10.2", "ஆறு புள்ளி ஒன்று பூஜ்ஜியம் புள்ளி இரண்டு", "figure_caption"),
    ("−12.05 cm", "எதிர்மறை பன்னிரண்டு புள்ளி பூஜ்ஜியம் ஐந்து cm", "table_row"),
    ("-.5", "எதிர்மறை பூஜ்ஜியம் புள்ளி ஐந்து", "table_row"),
    ("௩௬௦", "முந்நூற்று அறுபது", "list_item"),
    ("90°", "தொண்ணூறு °", "paragraph"),
    ("1,23,456", "ஒரு லட்சத்து இருபத்துமூன்று ஆயிரத்து நானூற்று ஐம்பத்தாறு", "table_row"),
    ("12cm, AB6, AB-6", "பன்னிரண்டு cm, AB6, AB-6", "paragraph"),
    ("$0.5$; $f(1, 2)$", "பூஜ்ஜியம் புள்ளி ஐந்து; $f(1, 2)$", "paragraph"),
])
def test_tamil_script_spells_numeric_values_by_place_value(text, expected, policy):
    [section] = script_for([entry(text, policy=policy)], unexpected_translation, target="ta").sections
    [chunk] = section.chunks
    assert chunk.text == expected
    assert not section.issues


@pytest.mark.parametrize(("language", "text", "number_words", "suffix"), [
    ("hi", "५.१०", "पाँच दशमलव एक शून्य", ""),
    ("bn", "৬.১", "ছয় দশমিক এক", ""),
    ("bn", "৫টি", "পাঁচ", " টি"),
    ("en", "6.1", "six point one", ""),
])
def test_script_uses_target_words_for_dotted_values(language, text, number_words, suffix):
    [section] = script_for([entry(f"({text}). AB-6")], unexpected_translation, target=language).sections
    [chunk] = section.chunks
    assert chunk.text == f"({number_words}{suffix}). AB-6"
    [number] = chunk.spoken_numbers
    assert number.text == number_words


@pytest.mark.parametrize("language", tuple(LANGUAGES))
def test_all_narration_languages_spell_checked_values(language):
    [section] = script_for([entry("−6.01 and 1,23,456")], unexpected_translation, target=language).sections
    [chunk] = section.chunks
    assert not section.issues
    assert all(not char.isdecimal() for char in chunk.text)
    assert [number.value for number in chunk.spoken_numbers] == ["-6.01", "123456"]
    if language != "en":
        assert all(any(not char.isascii() for char in number.text) for number in chunk.spoken_numbers)
        assert all("point" not in number.text and "minus" not in number.text for number in chunk.spoken_numbers)


@pytest.mark.parametrize(("max_chars", "count"), [(300, 2), (195, 3)])
def test_tamil_packs_adjacent_prose_slices_without_changing_text_or_provenance(max_chars, count):
    sentence = "கோடுகளை " * 12 + "வரைக."
    text = " ".join([sentence] * 3)
    [section] = script_for([entry(text)], unexpected_translation, target="ta", max_chars=max_chars).sections
    chunks = section.chunks
    assert len(chunks) == count
    assert chunks[0].text == (f"{sentence} {sentence} " if count == 2 else f"{sentence} ")
    assert "".join(chunk.text for chunk in chunks) == text
    assert all(len(chunk.text) <= min(max_chars, 240) for chunk in chunks)
    assert all(chunk.source_block_ids == chunk.unit_ids == ("p001-b001",) for chunk in chunks)


@pytest.mark.parametrize(("prefix", "policy"), [
    ("90° ", "paragraph"), ("AB ", "paragraph"), ("বাহু ", "paragraph"), ("", "figure_caption"),
])
def test_tamil_keeps_numeric_label_and_figure_chunks_short(prefix, policy):
    sentence = prefix + "கோடுகளை " * 12 + "வரைக."
    text = " ".join([sentence] * 3)
    [section] = script_for([entry(text, policy=policy)], unexpected_translation, target="ta").sections
    assert len(section.chunks) == 3
    assert all(len(chunk.text) <= 180 for chunk in section.chunks)
    assert "".join(chunk.text for chunk in section.chunks) == text.replace("90°", "தொண்ணூறு °")
    if prefix == "90° ":
        assert all(chunk.spoken_numbers == (SpokenNumber("90", "தொண்ணூறு"),) for chunk in section.chunks)


def test_new_english_uses_indic_translate_and_restores_checked_literals():
    calls = []

    def translate(unit_id, source, target, payload):
        calls.append((unit_id, source, target))
        label, figure, quantity = TOKEN.findall(payload)
        return TranslationResponse(f"{figure} में {label} की लंबाई {quantity} है।", ("a000001",))

    draft = entry("AB in Figure 1 measures 12 cm.", action="translate_english", policy="figure_description")
    result = script_for([draft], translate)
    assert calls == [("s001:n01", "en", "hi")]
    [chunk] = result.sections[0].chunks
    assert chunk.text == "Figure एक में AB की लंबाई बारह cm है।"
    assert chunk.spoken_numbers == (SpokenNumber("1", "एक"), SpokenNumber("12", "बारह"))
    assert chunk.translation_attempt_ids == ("a000001",)


def test_failed_literal_check_uses_the_checked_figure_caption():
    draft = entry("AB measures 12 cm.", action="translate_english", policy="figure_description",
                  fallback_text="AB की लंबाई 12 cm है।")

    def missing_literals(unit_id, source, target, payload):
        return TranslationResponse("गलत पाठ।", ("a000002",))

    [section] = script_for([draft], missing_literals).sections
    assert [chunk.text for chunk in section.chunks] == ["AB की लंबाई बारह cm है।"]
    [issue] = section.issues
    assert issue.disposition == "used_fallback"
    assert "Placeholder" in issue.reason
    assert issue.translation_attempt_ids == ("a000002",)


def test_splitting_prefers_sentences_then_clauses_and_preserves_math_and_quantities():
    text = "First. Measure 12.5 cm, then use $f(1, 2)$। अगला 3,400 kg؟ फिर सुनें।"
    parts = split_for_speech(text, max_chars=26)

    assert parts[0] == "First. "
    assert parts[1].rstrip() == "Measure 12.5 cm,"
    assert "".join(parts) == text
    assert all(0 < len(part) <= 26 for part in parts)
    assert all(any(group in part for part in parts) for group in ("12.5 cm", "3,400 kg", "$f(1, 2)$"))


def test_every_source_section_has_chunks_or_an_explicit_reason_and_audio_is_pending():
    result = script_for([
        entry(None, section="s002", action="skip", policy="unavailable", reason="OCR block is quarantined."),
        entry("First sentence. Second sentence."),
        entry("No punctuation or safe boundary anywhere in this unit"),
    ], unexpected_translation, sections=("s001", "s002", "s003"), max_chars=18, voice="Suhani")

    assert result.voice == "Suhani" and result.status == "completed_with_misses"
    first, skipped, empty = result.sections
    assert [section.section_id for section in result.sections] == ["s001", "s002", "s003"]
    assert [chunk.id for chunk in first.chunks] == ["s001-c01", "s001-c02"]
    assert "cannot be split safely" in first.issues[0].reason
    assert skipped.chunks == () and skipped.reason == "OCR block is quarantined."
    assert empty.chunks == () and empty.reason
    assert all(section.duration_seconds is None and section.speech_qa is None for section in result.sections)
    assert all(chunk.duration_seconds is None and chunk.speech_qa is None for chunk in first.chunks)


def test_script_checkpoint_writes_voice_text_and_translation_attempts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    monkeypatch.setenv("BODHAN_SPEECH_MAX_CHARS", "100")
    monkeypatch.delenv("BODHAN_TRANSLATE_GGUF_URL", raising=False)
    for setting in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(setting, raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")
    voices = tmp_path / "voices.yaml"
    voices.write_text("hi: Suhani\n", encoding="utf-8")

    def recognize(self, png, options):
        return {"markdown": "# Geometry\n\n$$a/(b+c)$$"}

    def translate(self, text, target):
        assert target == "hi"
        if text == "Geometry":
            return ModelTranslation("ज्यामिति")
        a, b, c = TOKEN.findall(text)
        return ModelTranslation(f"{a} को खुले कोष्ठक {b} जोड़ {c} बंद कोष्ठक से भाग दें।")

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    out = tmp_path / "out"
    assert main(["--input", str(scans), "--out", str(out), "--langs", "hi", "--source-lang", "en",
                 "--voices", str(voices), "--until", "scripts"]) == 0

    artifact = json.loads((out / "hi" / "narration_script.hi.json").read_text(encoding="utf-8"))
    assert (out / "work" / "narration-plan.json").is_file()
    assert artifact["language"] == "hi" and artifact["voice"] == "Suhani" and artifact["max_chars"] == 100
    [section] = artifact["sections"]
    heading, equation = section["chunks"]
    assert heading["text"] == "ज्यामिति"
    assert equation["text"] == "a को खुले कोष्ठक b जोड़ c बंद कोष्ठक से भाग दें।"
    assert equation["translation_attempt_ids"] == [artifact["attempts"][0]["id"]]
    assert section["duration_seconds"] is None and equation["speech_qa"] is None
