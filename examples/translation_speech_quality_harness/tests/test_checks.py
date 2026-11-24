"""test_checks_text: Tier-1 text checks. Every check: clean real Bodhan output must NOT flag; a
test_checks_audio: Audio checks on synthetic speech-like signals (no audio files in the repo).
test_roundtrip: Block 5: round-trip comparisons, noise separation, router. Real Bodhan outputs from fixtures.
"""

import asyncio
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from bodhan_eval.audio import analyze, read_audio, write_wav
from bodhan_eval.checks import REGISTRY, CheckContext, Flag, english_number_words, extract_numbers, register, run_checks
from bodhan_eval.clients import BodhanClient
from bodhan_eval.config import DEFAULTS
from bodhan_eval.dataset import Item
from bodhan_eval.infra import RunStats
from bodhan_eval.roundtrip import (
    CHECKER_NOISE,
    SYSTEM_ERROR,
    UNCERTAIN,
    chrf_pr,
    compare_back_translation,
    compare_transcript,
    compute_floors,
    decide,
    explain,
    run_round_trips,
    split_for_asr,
)

from .test_infra import Recorder

# ==================== checks_text
FX = json.loads((Path(__file__).parent / "fixtures" / "bodhan_outputs.json").read_text(encoding="utf-8"))


MD = json.loads((Path(__file__).parent / "fixtures" / "bodhan_markdown.json").read_text(encoding="utf-8"))


SRC = FX["source"]


def ctx(translation, source=SRC, lang="hi", **kw):
    return CheckContext("t1", lang, source, translation=translation, thresholds=dict(DEFAULTS), **kw)


def strong(flags, check=None):
    return [f for f in flags if not f.needs_corroboration and (check is None or f.check_id == check)]


@pytest.mark.parametrize("lang", ["hi", "ta", "bn"])
def test_real_short_outputs_are_clean(lang):
    flags, records = run_checks(ctx(FX["translations"][lang]["translation"], lang=lang))
    assert flags == [], [f.message for f in flags]
    assert all(r["status"] in ("ok", "skipped") for r in records)


@pytest.mark.parametrize("lang", ["hi", "bn"])
def test_real_markdown_outputs_are_clean(lang):
    flags, _ = run_checks(ctx(MD["translations"][lang], source=MD["source"], lang=lang))
    assert flags == [], [f.message for f in flags]


def test_number_extraction_and_grouping():
    assert [n.value for n in extract_numbers("1,00,000 and 100,000 and 3.50 and 10,20,30")] == ["100000", "100000", "3.5", "10", "20", "30"]
    assert [n.value for n in extract_numbers("code `x = 42` and $y = 7$ but 9 counts")] == ["9"]
    assert english_number_words("Two apples, twenty-five pears, two hundred cats, the first day") == ["2", "25", "200", "1"]


def test_changed_digit_is_localized():
    hi = FX["translations"]["hi"]["translation"]
    bad = hi.replace("100", "150")
    flags = strong(run_checks(ctx(bad))[0], "numbers")
    assert len(flags) == 1 and flags[0].category == "number_unit"
    s, e = flags[0].span
    assert bad[s:e] == "150"


def test_changed_digit_in_bengali_digits():
    bn = FX["translations"]["bn"]["translation"]
    one_zero_zero = "১০০"
    assert one_zero_zero in bn
    bad = bn.replace(one_zero_zero, "২০০")  # 100 -> 200, in Bengali digits
    flags = strong(run_checks(ctx(bad, lang="bn"))[0], "numbers")
    assert len(flags) == 1 and flags[0].evidence["translation_value"] == "200"


def test_unit_swap_flagged_with_unit_in_span():
    ta = FX["translations"]["ta"]["translation"]
    bad = ta.replace("100°C", "100°F")
    flags = strong(run_checks(ctx(bad, lang="ta"))[0], "numbers")
    assert len(flags) == 1 and "°F" in bad[flags[0].span[0]:flags[0].span[1]]


def test_missing_number_is_weak_and_spelled_out_source_is_ok():
    hi = FX["translations"]["hi"]["translation"]
    flags = run_checks(ctx(hi.replace("100 ", "")))[0]
    nums = [f for f in flags if f.check_id == "numbers"]
    assert len(nums) == 1 and nums[0].needs_corroboration
    assert nums[0].span is not None  # localized to the aligned sentence
    # English number word in the source, digit in the translation: fine
    assert run_checks(ctx(hi, source="Water boils at one hundred °C at sea level."), only={"numbers"})[0] == []


def test_untranslated_and_wrong_script():
    f = run_checks(ctx(SRC), only={"script"})[0]
    assert f[0].category == "wrong_language_script" and f[0].severity == "critical"
    f = run_checks(ctx(FX["translations"]["bn"]["translation"], lang="hi"), only={"script"})[0]
    assert f[0].category == "wrong_language_script" and f[0].evidence["dominant_script"] == "Bengali"
    assert run_checks(ctx("   "), only={"script"})[0][0].category == "omission"


def test_english_fragment_localized():
    hi = FX["translations"]["hi"]["translation"]
    bad = hi + " " + hi + " left in plain English here"
    f = strong(run_checks(ctx(bad), only={"script"})[0])
    assert len(f) == 1 and bad[f[0].span[0]:f[0].span[1]] == "left in plain English here"


def test_other_indic_script_fragment():
    hi = FX["translations"]["hi"]["translation"]
    bn_words = " ".join(FX["translations"]["bn"]["translation"].split()[:3])
    bad = hi + " " + bn_words
    f = run_checks(ctx(bad), only={"script"})[0]
    assert any(x.evidence.get("script") == "Bengali" for x in f)


def _md(lang="hi"):
    return MD["translations"][lang]


def test_deleted_dollar_flagged_on_its_line():
    t = _md()
    bad = t.replace("$F = ma$", "$F = ma", 1)
    f = run_checks(ctx(bad, source=MD["source"]), only={"format"})[0]
    assert f and all(x.category == "format" for x in f)
    s, e = f[0].span
    assert "F = ma" in bad[s:e] and "\n" not in bad[s:e]


def test_removed_table_pipe_flagged_on_row():
    t = _md("bn")
    lines = t.split("\n")
    row = next(i for i, ln in enumerate(lines) if ln.startswith("|") and "N" in ln)
    lines[row] = lines[row].rstrip().rstrip("|")
    bad = "\n".join(lines)
    f = run_checks(ctx(bad, source=MD["source"], lang="bn"), only={"format"})[0]
    assert len(f) == 1 and bad[f[0].span[0]:f[0].span[1]] == lines[row].strip()


def test_table_break_still_localized_when_lines_merge():
    t = _md()
    lines = t.split("\n")
    row = next(i for i, ln in enumerate(lines) if ln.startswith("|") and "kg" in ln)
    lines[row] = lines[row].replace("| kg |", " kg |")
    bad = "\n".join(lines[1:])  # heading removed: line counts no longer equal
    f = run_checks(ctx(bad, source=MD["source"]), only={"format"})[0]
    assert f and f[0].evidence["mode"] == "self_consistency" and "kg" in bad[f[0].span[0]:f[0].span[1]]


def test_changed_link_target_and_latex_body():
    t = _md()
    bad = t.replace("https://example.org/newton", "https://example.org/newtn")
    f = run_checks(ctx(bad, source=MD["source"]), only={"format"})[0]
    assert len(f) == 1 and "links" in f[0].message
    bad = t.replace("$F = ma$", "$F = mb$")
    f = run_checks(ctx(bad, source=MD["source"]), only={"format"})[0]
    assert len(f) == 1 and "F = mb" in bad[f[0].span[0]:f[0].span[1]]


def test_plain_prose_with_stray_dollar():
    hi = FX["translations"]["hi"]["translation"]
    f = run_checks(ctx(hi.replace(" ", " $", 1)), only={"format"})[0]
    assert len(f) == 1


def test_length_ratio_outlier_is_weak_signal():
    stats = RunStats()
    for i in range(12):
        stats.add("log_length_ratio", "hi", f"i{i}", 0.35 + 0.01 * (i % 3))
    stats.add("log_length_ratio", "hi", "t1", -0.9)
    c = ctx(FX["translations"]["hi"]["translation"], stats=stats)
    f = run_checks(c, only={"length_ratio"})[0]
    assert len(f) == 1 and f[0].category == "omission" and f[0].needs_corroboration
    # too few items of this language: no decision
    assert run_checks(ctx(FX["translations"]["hi"]["translation"]), only={"length_ratio"})[0] == []


def test_api_truncation_and_reference_metric():
    hi = FX["translations"]["hi"]["translation"]
    c = ctx(hi, translation_meta={"truncated": True}, reference=hi)
    flags, _ = run_checks(c, only={"api_truncation", "reference_chrf"})
    assert len(flags) == 1 and flags[0].span == (len(hi), len(hi))
    assert c.metrics["reference_chrf"]["chrf"] == pytest.approx(100.0)


def test_registry_isolates_broken_checks_and_is_extensible():
    @register("zz_test_broken", needs=("translation",))
    def broken(_ctx):
        raise RuntimeError("boom")

    @register("zz_test_new", needs=("translation",))
    def new_check(c):
        return [Flag("zz_test_new", "format", "minor", (0, 1), "chars", "demo", {})]

    try:
        flags, records = run_checks(ctx(FX["translations"]["hi"]["translation"]), only={"zz_test_broken", "zz_test_new", "numbers"})
        status = {r["check"]: r["status"] for r in records}
        assert status == {"numbers": "ok", "zz_test_broken": "error", "zz_test_new": "ok"}
        assert [f.check_id for f in flags] == ["zz_test_new"]
    finally:
        REGISTRY.pop("zz_test_broken")
        REGISTRY.pop("zz_test_new")


def test_flag_validates_schema():
    with pytest.raises(ValueError):
        Flag("x", "not_a_category", "major", None, "chars", "m")
    with pytest.raises(ValueError):
        Flag("x", "format", "huge", None, "chars", "m")


# ==================== checks_audio
SR = 24000


def speechlike(seconds=4.0, seed=0):
    """Harmonic 'voiced' signal with a syllable-rate envelope and short pauses."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    voiced = sum(np.sin(k * phase) / k for k in range(1, 8))
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t) ** 2
    x = 0.25 * voiced * env
    for p in rng.uniform(0.5, seconds - 0.5, 3):  # natural short pauses (0.15 s)
        x[int(p * SR): int((p + 0.15) * SR)] = 0
    return x.astype(np.float32)


def run_audio(x, text="a" * 60, stats=None, item="t1"):
    th = dict(DEFAULTS)
    c = CheckContext(item, "hi", "src", translation=text, audio=analyze(x, SR, th), audio_text=text, stats=stats or RunStats(), thresholds=th)
    return run_checks(c, only={"audio_sanity", "audio_duration"})[0]


def test_clean_speechlike_audio_has_no_flags():
    assert run_audio(speechlike()) == []


def test_inserted_long_silence_is_localized():
    x = speechlike()
    gap = np.zeros(int(2.0 * SR), dtype=np.float32)
    y = np.concatenate([x[: 2 * SR], gap, x[2 * SR:]])
    f = run_audio(y)
    assert len(f) == 1 and f[0].category == "speech_garbled"
    s, e = f[0].span
    assert abs(s - 2.0) < 0.1 and abs(e - 4.0) < 0.1


def test_clipping_detected():
    y = speechlike() * 6
    y = np.clip(y, -1, 1)
    f = run_audio(y)
    assert f and all(x.category == "speech_garbled" for x in f) and "Clipping" in f[0].message


def test_noise_burst_is_localized():
    x = speechlike()
    rng = np.random.default_rng(1)
    x[int(1.5 * SR): int(2.5 * SR)] = 0.2 * rng.standard_normal(SR).astype(np.float32)
    f = run_audio(x)
    noise = [g for g in f if "Noise-like" in g.message]
    assert len(noise) == 1
    s, e = noise[0].span
    assert s == pytest.approx(1.5, abs=0.15) and e == pytest.approx(2.5, abs=0.15)


def test_silent_audio_is_critical():
    f = run_audio(np.zeros(SR * 2, dtype=np.float32))
    assert len(f) == 1 and f[0].severity == "critical"


def test_duration_outliers_are_weak_signals():
    th = dict(DEFAULTS)
    stats = RunStats()
    text = "a" * 60
    for i in range(10):
        a = analyze(speechlike(4.0 + 0.05 * i, seed=i), SR, th)
        stats.add("speech_s_per_char", "hi", f"i{i}", (a.duration_s - a.leading_silence_s - a.trailing_silence_s) / 60)
    short = speechlike(2.0)
    a = analyze(short, SR, th)
    stats.add("speech_s_per_char", "hi", "t1", (a.duration_s - a.leading_silence_s - a.trailing_silence_s) / 60)
    f = run_audio(short, text=text, stats=stats)
    assert len(f) == 1 and f[0].category == "speech_omission_truncation" and f[0].needs_corroboration


# ==================== roundtrip
FIX = json.loads((Path(__file__).parent / "fixtures" / "bodhan_outputs.json").read_text(encoding="utf-8"))


SRC = FIX["source"]


TR = {k: v["translation"] for k, v in FIX["translations"].items()}


BT = {k: v["back_translation"] for k, v in FIX["translations"].items()}


TH = dict(DEFAULTS)


def test_chrf_precision_recall_direction():
    src = "The quick brown fox jumps over the lazy dog near the river bank."
    _, p_cut, r_cut = chrf_pr("The quick brown fox jumps over the lazy dog.", src)
    _, p_pad, r_pad = chrf_pr(src + " It was very happy and sang loudly all day.", src)
    assert p_cut > 90 and r_cut < 75, "truncation: precision high, recall low (omission)"
    assert r_pad == pytest.approx(100.0) and p_pad < 70, "padding: recall high, precision low (addition)"


def test_real_back_translations_preserve_numbers():
    for lang in ("hi", "ta", "bn"):
        bt = compare_back_translation(SRC, BT[lang])
        assert "100" not in bt.numbers_missing, lang
        assert bt.recall > 50


def test_entity_masking_is_the_proper_noun_exemption():
    src = "The meeting with Raonic was held in Brisbane on Monday."
    back = "The meeting with Raonik was held in Brizbane on Monday."  # names transliterated back differently
    bt = compare_back_translation(src, back)
    assert set(bt.entities_missing) == {"Raonic", "Brisbane"}
    assert bt.recall_masked > bt.recall and bt.precision_masked > bt.precision and bt.recall_masked > 95


def test_real_asr_pair_digit_vs_word_is_noise():
    asr = FIX["asr"]
    res = compare_transcript(asr["reference_text"], asr["asr_text"], TH)
    assert res.wer > 0, "raw WER sees the '100' -> number words difference"
    assert res.residual_wer == 0 and res.residual_cer == 0
    assert [r.noise for r in res.regions] == ["digit_vs_word"]
    assert res.tail_deleted == 0 and res.repeat is None


def test_matra_only_difference_is_noise():
    word = TR["hi"].split()[0]
    stripped = "".join(c for c in word if not ("ा" <= c <= "्"))  # drop vowel signs (code-point range, no invented text)
    assert explain([word], [stripped], TH) == "matra_zwj"


def test_tail_delete_and_repeat_detected():
    words = TR["hi"].split()
    cut = compare_transcript(TR["hi"], " ".join(words[:-3]), TH)
    assert cut.tail_deleted >= 2 and cut.residual_wer > 0
    looped = " ".join(words[:4] + words[2:4] + words[4:])
    rep = compare_transcript(TR["hi"], looped, TH)
    assert rep.repeat is not None and rep.repeat["words"] == 2 and rep.tail_deleted == 0


def test_floors_per_language_or_pooled():
    obs = [("bt_recall", "hi", float(v)) for v in range(15)] + [("bt_recall", "ta", float(v)) for v in range(3)]
    floors = compute_floors(obs, ["hi", "ta", "te"], TH)
    assert floors["bt_recall"]["hi"]["source"] == "lang" and floors["bt_recall"]["hi"]["n"] == 15
    assert floors["bt_recall"]["ta"]["source"] == "pooled" and floors["bt_recall"]["te"]["n"] == 18
    assert floors["asr_residual_wer"]["hi"]["source"] == "insufficient"
    assert compute_floors(obs, ["hi", "ta"], TH) == compute_floors(list(reversed(obs)), ["ta", "hi"], TH)


def test_drift_alone_never_system_error():
    assert decide("bt_recall", 10.0, 50.0, explained_by=[], corroborated_by=[]).verdict == UNCERTAIN
    assert decide("bt_recall", 10.0, 50.0, explained_by=[], corroborated_by=["length_ratio"]).verdict == SYSTEM_ERROR
    assert decide("bt_recall", 60.0, 50.0, explained_by=[], corroborated_by=["x"]).verdict == CHECKER_NOISE
    assert decide("asr_residual_wer", 0.9, None, explained_by=[], corroborated_by=["x"]).reasons == ["no_noise_floor"]


def speech(seconds=4.0, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    on = (t % 0.4) < 0.3
    return (0.3 * np.sin(2 * np.pi * 220 * t) * on).astype(np.float32), sr


def ctx_for(transcript, *, duration, z_series, prior=()):
    s, sr = speech(duration)
    prof = analyze(s, sr, TH)
    stats = RunStats()
    for i, v in enumerate(z_series):
        stats.add("speech_s_per_char", "hi", f"o{i}", v)
    chars = len(TR["hi"].replace(" ", ""))
    stats.add("speech_s_per_char", "hi", "x", (prof.duration_s - prof.leading_silence_s - prof.trailing_silence_s) / chars)
    cc = CheckContext("x", "hi", SRC, translation=TR["hi"], audio=prof, audio_text=TR["hi"], stats=stats, thresholds=TH, asr_text=transcript)
    cc.asr = compare_transcript(TR["hi"], transcript, TH)
    cc.prior_flags = list(prior)
    cc.floors = {"asr_residual_wer": 0.2, "asr_residual_cer": 0.2}
    return cc


def test_truncation_needs_acoustic_confirmation():
    words = TR["hi"].split()
    normal = [0.1] * 10  # other clips: 0.1 s per char
    short = ctx_for(" ".join(words[:-3]), duration=1.0, z_series=normal)  # clip much shorter than the norm
    flags, _ = run_checks(short, tier=2)
    trunc = [f for f in flags if f.category == "speech_omission_truncation"]
    assert trunc and not trunc[0].needs_corroboration and trunc[0].unit == "seconds"
    assert trunc[0].span[1] == pytest.approx(short.audio.duration_s, abs=1e-3)
    chars = len(TR["hi"].replace(" ", ""))
    same = ctx_for(" ".join(words[:-3]), duration=4.0, z_series=[3.6 / chars] * 10)  # duration is normal
    flags, _ = run_checks(same, tier=2)
    assert all(f.needs_corroboration for f in flags if f.category == "speech_omission_truncation"), "ASR alone stays uncertain"


def test_clean_transcript_no_flags():
    cc = ctx_for(FIX["asr"]["asr_text"], duration=4.0, z_series=[0.1] * 10)
    cc.asr = compare_transcript(FIX["asr"]["reference_text"], FIX["asr"]["asr_text"], TH)
    flags, _ = run_checks(cc, tier=2)
    assert flags == []
    assert any(v["verdict"] == CHECKER_NOISE for v in cc.metrics["verdicts"])


def test_back_translation_verdicts_need_corroboration():
    cc = CheckContext("x", "hi", SRC, translation=TR["hi"], back_translation="Water boils.", thresholds=TH)
    cc.bt = compare_back_translation(SRC, "Water boils.")
    cc.floors = {"bt_recall": 50.0, "bt_precision": 50.0}
    flags, _ = run_checks(cc, tier=2)
    assert [f.needs_corroboration for f in flags if f.category == "omission"] == [True]
    cc2 = CheckContext("x", "hi", SRC, translation=TR["hi"], back_translation="Water boils.", thresholds=TH)
    cc2.bt, cc2.floors = cc.bt, cc.floors
    cc2.prior_flags = [Flag("length_ratio", "omission", "major", None, "chars", "short", {}, 0.5, needs_corroboration=True)]
    flags, _ = run_checks(cc2, tier=2)
    assert [f.needs_corroboration for f in flags if f.category == "omission"] == [False]
    cc3 = CheckContext("x", "hi", SRC, translation=TR["hi"], back_translation="Water boils.", bt_truncated=True, thresholds=TH)
    cc3.bt, cc3.floors = cc.bt, cc.floors
    flags, _ = run_checks(cc3, tier=2)
    assert flags == [] and cc3.metrics["verdicts"][0]["reasons"] == ["back_translation_truncated"]


def test_split_for_asr_long_audio():
    s, sr = speech(70.0, 16000)
    pieces = split_for_asr(write_wav(s, sr))
    durs = [len(read_audio(p)[0]) / sr for p in pieces]
    assert len(pieces) == 3 and all(d <= 30.0 for d in durs) and sum(durs) == pytest.approx(70.0, abs=0.01)
    assert len(split_for_asr(write_wav(s[: 5 * sr], sr))) == 1


def test_router_logs_every_stage_and_isolates_failures(settings, cache, ledger, tmp_path):
    s, sr = speech(2.0)
    wav = tmp_path / "a.wav"
    wav.write_bytes(write_wav(s, sr))

    class FailEn(Recorder):
        def __call__(self, request):
            if request.url.path == "/v1/chat/completions" and b'"target_language": "en"' in request.content.replace(b'":"', b'": "'):
                return httpx.Response(400, json={"error": {"message": "bad", "code": "invalid_request_error"}})
            return super().__call__(request)

    async def go():
        async with BodhanClient(settings, cache, ledger, transport=httpx.MockTransport(FailEn())) as c:
            a = await run_round_trips(c, Item("a", "s", "hi", translation="t", audio=wav), audio_decodable=True, generated=None, generate=False)
            b = await run_round_trips(c, Item("b", "s", "hi"), audio_decodable=False, generated=None, generate=False)
            return a, b

    a, b = asyncio.run(go())
    stages = {st["stage"]: st for st in a.route}
    assert set(stages) == {"tier1", "tts", "back_translation", "asr"}
    assert stages["back_translation"]["ran"] is False and stages["back_translation"]["reason"].startswith("error:")
    assert stages["asr"]["ran"] is True and a.asr_text == "transcript", "a failed stage does not stop the others"
    assert {st["stage"]: st["reason"] for st in b.route}["asr"] == "no audio"
    assert {st["stage"]: st["reason"] for st in b.route}["tts"] == "--generate not set"
