import json

from conftest import FakeBodhan, make_wav
from feedback_analyser.ingest import Item
from feedback_analyser.pipeline import Options, process_item, run, word_overlap
from feedback_analyser.report import summarise, write_all
from feedback_analyser.respond import draft_reply

HINDI_COMPLAINT = "खाना ठंडा था और डिलीवरी देर से आई।"
DELIVERY_REPLY_HI = "हमें खेद है कि आपका ऑर्डर समय पर नहीं पहुँचा।"


def fake(taxonomy, **kwargs):
    reply = taxonomy["themes"]["delivery"]["reply"]
    return FakeBodhan(
        to_english={
            HINDI_COMPLAINT: "The food was cold and the delivery came late.",
            "delivery bahut late thi yaar": "The delivery was very late, man.",
            DELIVERY_REPLY_HI: reply,
            "சேவை நன்றாக இருந்தது": "The service was good.",
        },
        to_language={(reply, "hi"): DELIVERY_REPLY_HI},
        **kwargs,
    )


def test_native_script_complaint_gets_a_checked_reply_but_no_audio_for_text(tmp_path, taxonomy, analyser):
    client = fake(taxonomy)
    record = process_item(Item("t1", "text", HINDI_COMPLAINT), client, analyser, taxonomy, Options(out_dir=tmp_path))
    assert (record["language"], record["script"], record["language_source"]) == ("hi", "Devanagari", "script")
    assert record["sentiment"] == "negative" and set(record["themes"]) == {"quality", "delivery"}
    reply = record["reply"]
    assert reply["text"] == DELIVERY_REPLY_HI and reply["fidelity"] == 1.0 and not reply["needs_review"] and reply["audio"] is None
    assert not any(call[0] in ("speak", "transliterate") for call in client.log)


def test_roman_script_customer_is_answered_in_roman_script(tmp_path, taxonomy, analyser):
    client = fake(taxonomy)
    record = process_item(Item("t2", "text", "delivery bahut late thi yaar"), client, analyser, taxonomy, Options(out_dir=tmp_path))
    assert (record["language"], record["script"], record["language_source"]) == ("hi", "Latin", "default")
    assert any("assumed" in flag for flag in record["flags"])
    assert record["reply"]["text"] == f"roman({DELIVERY_REPLY_HI})" and record["reply"]["native"] == DELIVERY_REPLY_HI


def test_english_is_recognised_without_a_hint_and_kept_verbatim(tmp_path, taxonomy, analyser):
    client = fake(taxonomy)
    record = process_item(Item("t3", "text", "The staff were very polite."), client, analyser, taxonomy, Options(out_dir=tmp_path))
    assert (record["language"], record["language_source"], record["english"]) == ("en", "translation check", "The staff were very polite.")
    assert record["reply"] is None
    hinted = process_item(Item("t4", "text", "The staff were rude.", language="en"), FakeBodhan(), analyser, taxonomy, Options(out_dir=tmp_path))
    assert hinted["reply"]["text"] == taxonomy["themes"]["staff"]["reply"] and hinted["reply"]["fidelity"] is None


def test_voice_complaint_is_transcribed_and_answered_with_audio(tmp_path, taxonomy, analyser):
    clip = tmp_path / "v1.wav"
    clip.write_bytes(make_wav(1))
    client = fake(taxonomy, transcripts={"v1.wav": HINDI_COMPLAINT})
    record = process_item(Item("v/1", "voice", file=clip, language="hi"), client, analyser, taxonomy, Options(out_dir=tmp_path))
    assert ("transcribe", "v1.wav", "hi") in client.log and ("speak", "hi", "Kavya", "Customer Care") in client.log
    assert record["reply"]["audio"] == "replies/v_1.wav" and (tmp_path / "replies" / "v_1.wav").read_bytes()[:4] == b"RIFF"
    silent = process_item(Item("v2", "voice", file=clip), fake(taxonomy, transcripts={"v1.wav": HINDI_COMPLAINT}), analyser, taxonomy,
                          Options(out_dir=tmp_path, speak="none"))
    assert silent["reply"]["audio"] is None


def test_card_with_low_ocr_confidence_is_flagged(tmp_path, taxonomy, analyser):
    client = fake(taxonomy, pages={"c1.png": {"text": "சேவை நன்றாக இருந்தது", "confidence": 0.41}})
    record = process_item(Item("c1", "card", file=tmp_path / "c1.png"), client, analyser, taxonomy, Options(out_dir=tmp_path))
    assert record["language"] == "ta" and record["sentiment"] == "positive" and record["themes"] == ["staff"]
    assert any("low OCR confidence" in flag for flag in record["flags"])


def test_unfaithful_or_wrong_script_reply_is_held_for_review_and_not_voiced(taxonomy):
    template = taxonomy["themes"]["delivery"]["reply"]
    drifted = FakeBodhan(to_language={(template, "hi"): "मौसम अच्छा है।"}, to_english={"मौसम अच्छा है।": "The weather is nice."})
    reply = draft_reply(drifted, template, "hi", "Devanagari")
    assert reply["needs_review"] and "back-translation" in reply["review_reasons"][0]
    untranslated = draft_reply(FakeBodhan(to_language={(template, "ta"): template}), template, "ta", "Tamil")
    assert any("Tamil script" in reason for reason in untranslated["review_reasons"])
    unsupported = draft_reply(FakeBodhan(), template, "bho", "Devanagari")
    assert unsupported["needs_review"] and unsupported["text"] == template


def test_one_bad_item_does_not_stop_the_batch_and_order_is_kept(tmp_path, taxonomy, analyser):
    clip = tmp_path / "bad.wav"
    clip.write_bytes(b"not audio")
    items = [Item("a", "text", HINDI_COMPLAINT), Item("b", "voice", file=clip), Item("c", "text", "🙂🙂"), Item("d", "text", "Thanks, great service!", language="en")]
    records = run(items, fake(taxonomy, fail_on={"bad.wav"}), analyser, taxonomy, Options(out_dir=tmp_path, workers=3), progress=None)
    assert [record["id"] for record in records] == ["a", "b", "c", "d"]
    assert "could not be decoded" in records[1]["error"] and "no readable text" in records[2]["error"]
    assert records[0]["error"] is None and records[3]["sentiment"] == "positive"


def test_report_files_summarise_and_escape(tmp_path, taxonomy, analyser):
    items = [Item("a", "text", HINDI_COMPLAINT), Item("b", "text", "<script>alert(1)</script> great service", language="en"), Item("c", "text", "9876543210 🙂")]
    records = run(items, fake(taxonomy), analyser, taxonomy, Options(out_dir=tmp_path), progress=None)
    summary = write_all(tmp_path, records, taxonomy, {"calls": {"indic-translate": 3}, "cache_hits": {}})
    assert summary == summarise(records, taxonomy)
    assert (summary["total"], summary["analysed"], summary["errors"], summary["replies"]) == (3, 2, 1, 1)
    assert summary["themes"]["delivery"]["negative"] == 1 and summary["languages"] == {"hi": 1, "en": 1}
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<script>alert" not in html and "&lt;script&gt;" in html and DELIVERY_REPLY_HI in html
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["summary"] == summary and len(saved["items"]) == 3
    assert (tmp_path / "report.csv").read_text(encoding="utf-8-sig").count("\n") == 4


def test_word_overlap():
    assert word_overlap("The delivery was late", "the delivery was late.") == 1.0
    assert word_overlap("delivery bahut late thi", "The delivery was very late") < 0.7
    assert word_overlap("", "x") == 0.0
