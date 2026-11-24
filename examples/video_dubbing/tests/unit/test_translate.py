"""
Unit tests for translation checks and stage execution.
"""

from pathlib import Path

import pytest
from fake_bodhan.server import FakeBodhanTransport

from bodhan_dub.config import Settings
from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import BudgetGuard, CostLedger
from bodhan_dub.gateway.llm import LLMClient
from bodhan_dub.gateway.resilience import CircuitBreakerOpen
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.stages.glossary import Glossary
from bodhan_dub.stages.segment import SegmentSlot
from bodhan_dub.stages.translate import (
    TranslationStage,
    check_dominant_script,
    run_translation_structural_checks,
)


@pytest.fixture
def registry():
    yaml_path = Path(__file__).resolve().parent.parent.parent / "languages.yaml"
    return LanguageRegistry(yaml_path)


def test_check_dominant_script(registry):
    # Valid Hindi (Devanagari)
    ok, _ = check_dominant_script("नमस्ते दुनिया", "hi", registry)
    assert ok is True

    # Valid Hindi with allowed Latin technical term and acronyms
    ok_latin, _ = check_dominant_script("यह AI और NASA का मॉडल है", "hi", registry)
    assert ok_latin is True

    # Pure English text passed as Hindi should fail script check
    bad, reason = check_dominant_script("This is pure English text.", "hi", registry)
    assert bad is False
    assert "dominant_script_mismatch" in reason

    # Valid Bengali script
    ok_bn, _ = check_dominant_script("কৃত্রিম বুদ্ধিমত্তা", "bn", registry)
    assert ok_bn is True

    # Devanagari passed for Bengali must fail with different_script_dominated
    bad_bn, r_bn = check_dominant_script("नमस्ते दुनिया", "bn", registry)
    assert bad_bn is False
    assert "different_script_dominated" in r_bn

    # Valid Urdu in Arabic block
    ok_ur, _ = check_dominant_script("مصنوعی ذہانت", "ur", registry)
    assert ok_ur is True

    # Empty text has no letters
    bad_empty, reason_empty = check_dominant_script("12345 ???", "hi", registry)
    assert bad_empty is False
    assert reason_empty == "no_letters"


def test_run_translation_structural_checks(registry):
    src = "The speed of light is 300000 km per second."
    tgt_good = "प्रकाश की गति ३००००० किमी प्रति सेकंड है।"

    ok, reason = run_translation_structural_checks(src, tgt_good, "hi", registry)
    assert ok is True

    # Empty target
    bad_empty, r_empty = run_translation_structural_checks(src, "   ", "hi", registry)
    assert bad_empty is False
    assert r_empty == "empty_translation"

    # Verbatim copy
    bad_copy, r_copy = run_translation_structural_checks(src, src, "hi", registry)
    assert bad_copy is False
    assert r_copy == "verbatim_copy_of_source"

    # Number mismatch (dropped 300000)
    tgt_no_num = "प्रकाश की गति किमी प्रति सेकंड है।"
    bad_num, r_num = run_translation_structural_checks(src, tgt_no_num, "hi", registry)
    assert bad_num is False
    assert r_num == "number_mismatch"

    # Leftover placeholder
    tgt_ph = "प्रकाश की गति __TERM_0__ किमी प्रति सेकंड है।"
    bad_ph, r_ph = run_translation_structural_checks(src, tgt_ph, "hi", registry)
    assert bad_ph is False
    assert r_ph == "leftover_placeholders"

    # Abnormal length ratio (excessively short)
    bad_short, r_short = run_translation_structural_checks(
        "A very long detailed English sentence that goes on and on.",
        "छोटा",
        "hi",
        registry,
    )
    assert bad_short is False
    assert "abnormal_length_ratio" in r_short

    # Repeated n-gram looping
    src_loop = "Hello world and welcome to everyone in our community today."
    tgt_loop = "नमस्ते दुनिया नमस्ते दुनिया नमस्ते दुनिया नमस्ते दुनिया"
    bad_loop, r_loop = run_translation_structural_checks(src_loop, tgt_loop, "hi", registry)
    assert bad_loop is False
    assert "repeated_ngram_loop" in r_loop


@pytest.mark.asyncio
async def test_translation_stage_with_fake_bodhan(
    test_settings, test_cache, test_ledger, test_logger, registry
):
    transport = FakeBodhanTransport()
    b_client = BodhanClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        budget_guard=BudgetGuard(test_ledger),
        logger=test_logger,
        transport=transport,
    )
    llm_client = LLMClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        logger=test_logger,
        transport=transport,
    )

    try:
        stage = TranslationStage(
            bodhan_client=b_client,
            llm_client=llm_client,
            registry=registry,
            logger=test_logger,
        )

        seg = SegmentSlot(
            id="seg_0001",
            index=0,
            text="Welcome to artificial intelligence.",
            start=0.0,
            end=3.0,
            duration=3.0,
            slot=[0.0, 3.0],
            slack_before=0.0,
            slack_after=0.5,
            timestamp_source="api_verbose",
        )

        glossary = Glossary()
        translated_res = await stage.translate_segment(seg, target_lang="hi", glossary=glossary)
        assert translated_res.status == "ok"
        assert "स्वागत" in translated_res.target_text
    finally:
        await b_client.close()
        await llm_client.close()


@pytest.mark.asyncio
async def test_translation_stage_run_multiple_segments(
    test_settings, test_cache, test_ledger, test_logger, registry
):
    transport = FakeBodhanTransport()
    b_client = BodhanClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        budget_guard=BudgetGuard(test_ledger),
        logger=test_logger,
        transport=transport,
    )
    llm_client = LLMClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        logger=test_logger,
        transport=transport,
    )

    try:
        stage = TranslationStage(
            bodhan_client=b_client,
            llm_client=llm_client,
            registry=registry,
            logger=test_logger,
        )

        segments = [
            SegmentSlot(
                id="seg_0001",
                index=0,
                text="Welcome to artificial intelligence.",
                start=0.0,
                end=3.0,
                duration=3.0,
                slot=[0.0, 3.0],
                slack_before=0.0,
                slack_after=0.5,
            ),
            SegmentSlot(
                id="seg_0002",
                index=1,
                text="This is the second segment.",
                start=3.5,
                end=6.0,
                duration=2.5,
                slot=[3.5, 6.0],
                slack_before=0.5,
                slack_after=0.5,
            ),
        ]

        glossary = Glossary()
        results = await stage.run(segments=segments, target_lang="hi", glossary=glossary)
        assert len(results) == 2
        assert results[0].id == "seg_0001"
        assert results[1].id == "seg_0002"
        assert results[0].status == "ok"
        assert results[1].status == "ok"
    finally:
        await b_client.close()
        await llm_client.close()


@pytest.mark.asyncio
async def test_translation_stage_code_mixed_and_glossary(
    test_settings, test_cache, test_ledger, test_logger, registry
):
    transport = FakeBodhanTransport()
    b_client = BodhanClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        budget_guard=BudgetGuard(test_ledger),
        logger=test_logger,
        transport=transport,
    )
    llm_client = LLMClient(
        settings=test_settings,
        cache=test_cache,
        ledger=test_ledger,
        logger=test_logger,
        transport=transport,
    )

    try:
        stage = TranslationStage(
            bodhan_client=b_client,
            llm_client=llm_client,
            registry=registry,
            logger=test_logger,
            code_mixed=True,
        )

        seg = SegmentSlot(
            id="seg_0001",
            index=0,
            text="Welcome to artificial intelligence.",
            start=0.0,
            end=3.0,
            duration=3.0,
            slot=[0.0, 3.0],
            slack_before=0.0,
            slack_after=0.5,
        )

        glossary = Glossary()
        glossary.add_term(
            term="artificial intelligence",
            translations={"hi": "आर्टिफिशियल इंटेलिजेंस"},
        )

        translated_res = await stage.translate_segment(seg, target_lang="hi", glossary=glossary)
        assert translated_res.status == "ok"
        assert "आर्टिफिशियल" in translated_res.target_text
    finally:
        await b_client.close()
        await llm_client.close()


@pytest.mark.asyncio
async def test_translate_5xx_never_sends_english_to_tts(registry, test_logger, tmp_path):
    """
    Assert that when every translation call returns 5xx:
    1. Translation produces translation_failed with empty target_text (never falls back to English).
    2. SegmentFitController skips synthesis (0 speak API calls) and returns skipped status.
    """
    fake_transport = FakeBodhanTransport(failure_injection={"translate_500": True})
    settings = Settings(bodhan_base_url="https://api.bodhan.ai/v1")
    cache = DiskCache(cache_dir=tmp_path / "cache")
    ledger = CostLedger()
    guard = BudgetGuard(ledger)

    b_client = BodhanClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        budget_guard=guard,
        logger=test_logger,
        transport=fake_transport,
    )
    llm_client = LLMClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        logger=test_logger,
        transport=fake_transport,
    )

    try:
        stage = TranslationStage(
            bodhan_client=b_client,
            llm_client=llm_client,
            registry=registry,
            logger=test_logger,
        )

        seg = SegmentSlot(
            id="seg_err_01",
            index=0,
            text="This is an English sentence that fails translation.",
            start=0.0,
            end=3.0,
            duration=3.0,
            slot=[0.0, 3.0],
            slack_before=0.0,
            slack_after=0.5,
        )

        try:
            res = await stage.translate_segment(seg, target_lang="hi", glossary=Glossary())
            assert res.status == "translation_failed"
            assert res.target_text == ""
        except CircuitBreakerOpen:
            # CircuitBreakerOpen is re-raised per A1 specification
            pass

        # Now pass to fit controller with empty or Latin-only text
        from bodhan_dub.stages.fit import SegmentFitController

        fit_controller = SegmentFitController(
            bodhan_client=b_client,
            llm_client=llm_client,
            logger=test_logger,
            registry=registry,
        )

        fit_res, audio = await fit_controller.process_segment(
            segment_id=seg.segment_id,
            source_en=seg.text,
            initial_translation="",  # No target text produced
            slot_duration=seg.slot_duration,
            slack_before=seg.slack_before,
            slack_after=seg.slack_after,
            lang="hi",
            voice="Kavya",
        )

        # Assert no speak call was made
        assert fake_transport.call_counts["speak"] == 0
        assert fit_res.fit_status == "skipped"
        assert fit_res.qa_status == "skipped"
        assert audio is None

        # Also test that passing Latin-only text to process_segment is rejected before TTS
        fit_res_latin, audio_latin = await fit_controller.process_segment(
            segment_id="seg_latin",
            source_en=seg.text,
            initial_translation="This is pure English text passed by mistake.",
            slot_duration=seg.slot_duration,
            slack_before=seg.slack_before,
            slack_after=seg.slack_after,
            lang="hi",
            voice="Kavya",
        )
        assert fake_transport.call_counts["speak"] == 0
        assert fit_res_latin.fit_status == "skipped"
        assert audio_latin is None
    finally:
        await b_client.close()
        await llm_client.close()
