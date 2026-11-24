"""
Tests for the source_chars=0 verifier bug (Bug 1).

Ensures that:
1. verify_translation() returns status='error' when source_text is empty,
   instead of computing a meaningless inflated ratio.
2. verify_all() resolves source text from step.resolved_args["text"] rather
   than step.args["text"] when args contains an unresolved template ref.
3. verify_all() falls back to step.resolved_args["file_path"] when text is absent.
4. verify_all() emits status='skipped' (not a false warning) when source
   text truly cannot be resolved at all.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bodhan_agent.verifier import verify_translation, verify_all


# ---------------------------------------------------------------------------
# Unit tests for verify_translation()
# ---------------------------------------------------------------------------

class TestVerifyTranslationEmptySource:
    def test_empty_source_returns_error_status(self):
        result = verify_translation(
            source_text="",
            translated_text="नमस्ते दुनिया",
            target_language="hi",
        )
        assert result["status"] == "error", (
            f"Expected status='error' for empty source, got {result['status']!r}. "
            "If source_chars is 0 we must not compute a ratio."
        )
        assert result.get("source_chars", 0) == 0

    def test_whitespace_only_source_returns_error_status(self):
        result = verify_translation(
            source_text="   \n\t  ",
            translated_text="Hello world",
            target_language="en",
        )
        assert result["status"] == "error"

    def test_non_empty_source_does_not_return_error(self):
        result = verify_translation(
            source_text="Good morning",
            translated_text="Bonjour",
            target_language="fr",
        )
        assert result["status"] != "error"
        assert result.get("source_chars", 0) > 0

    def test_ratio_not_inflated_for_known_good_translation(self):
        # Punjabi is typically 0.8–1.2x Hindi in length
        hindi = "सुप्रभात, आपका दिन शुभ हो।"
        punjabi = "ਸ਼ੁਭ ਸਵੇਰ, ਤੁਹਾਡਾ ਦਿਨ ਸ਼ੁਭ ਹੋਵੇ।"
        result = verify_translation(hindi, punjabi, "pa")
        assert result["status"] in ("passed", "warning")
        # Ratio should be sane (0.2–4.0), not 8+ like the bug produced
        assert result["length_ratio"] <= 4.0, (
            f"Inflated ratio {result['length_ratio']} — source_chars bug may still be present"
        )


# ---------------------------------------------------------------------------
# Integration tests for verify_all() — fake TaskState
# ---------------------------------------------------------------------------

def _make_state(
    *,
    step_args: dict,
    step_resolved_args: dict,
    artifact_kind: str = "markdown",
    artifact_text: str = "नमस्ते दुनिया यह एक परीक्षण है।",
    input_text: str | None = None,
):
    """Build a minimal fake TaskState for verify_all tests."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(artifact_text)
        artifact_path = f.name

    step = SimpleNamespace(
        id="step_1",
        tool="translate_text",
        args=step_args,
        resolved_args=step_resolved_args,
    )
    artifact = SimpleNamespace(
        kind=artifact_kind,
        path=artifact_path,
        produced_by="step_1",
    )
    state = SimpleNamespace(
        artifacts=[artifact],
        input_text=input_text,
        get_step=lambda sid: step if sid == "step_1" else None,
    )
    return state, artifact_path


class TestVerifyAllSourceResolution:
    """verify_all must resolve source text from resolved_args, not raw args."""

    @pytest.mark.asyncio
    async def test_uses_resolved_args_text_not_raw_template(self):
        """When args has a template placeholder, resolved_args has the real text."""
        source = "Good morning, have a wonderful day."
        state, path = _make_state(
            step_args={"text": "${step_0.output}", "target_language": "hi"},
            step_resolved_args={"text": source, "target_language": "hi"},
        )
        try:
            results = await verify_all(state, out_dir="/tmp")
        finally:
            Path(path).unlink(missing_ok=True)

        trans_checks = [r for r in results if r["check"] == "translation_verification"]
        assert trans_checks, "Expected at least one translation_verification result"
        r = trans_checks[0]
        assert r["status"] != "error", "Should not error when resolved_args has real text"
        assert r.get("source_chars", 0) == len(source.strip()), (
            f"source_chars should be {len(source.strip())}, got {r.get('source_chars')}"
        )
        # Ratio must be sane — not inflated like the old bug
        assert r["length_ratio"] <= 4.0, f"Inflated ratio: {r['length_ratio']}"

    @pytest.mark.asyncio
    async def test_falls_back_to_file_path_when_text_missing(self):
        """When resolved_args has no 'text' but has 'file_path', read that file."""
        source = "नमस्ते, आपका स्वागत है।"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as sf:
            sf.write(source)
            src_path = sf.name

        state, art_path = _make_state(
            step_args={"file_path": src_path, "target_language": "pa"},
            step_resolved_args={"file_path": src_path, "target_language": "pa"},
        )
        try:
            results = await verify_all(state, out_dir="/tmp")
        finally:
            Path(art_path).unlink(missing_ok=True)
            Path(src_path).unlink(missing_ok=True)

        trans_checks = [r for r in results if r["check"] == "translation_verification"]
        assert trans_checks
        r = trans_checks[0]
        assert r["status"] != "error"
        assert r.get("source_chars", 0) == len(source.strip())

    @pytest.mark.asyncio
    async def test_falls_back_to_state_input_text(self):
        """When resolved_args is empty, fall back to state.input_text."""
        source = "Have a great day!"
        state, art_path = _make_state(
            step_args={"target_language": "hi"},
            step_resolved_args={},
            input_text=source,
        )
        try:
            results = await verify_all(state, out_dir="/tmp")
        finally:
            Path(art_path).unlink(missing_ok=True)

        trans_checks = [r for r in results if r["check"] == "translation_verification"]
        assert trans_checks
        r = trans_checks[0]
        assert r["status"] != "error"
        assert r.get("source_chars", 0) == len(source.strip())

    @pytest.mark.asyncio
    async def test_skipped_when_source_truly_unresolvable(self):
        """When nothing resolves source text, emit status='skipped', not a false warning."""
        state, art_path = _make_state(
            step_args={"target_language": "hi"},
            step_resolved_args={},
            input_text=None,
        )
        try:
            results = await verify_all(state, out_dir="/tmp")
        finally:
            Path(art_path).unlink(missing_ok=True)

        trans_checks = [r for r in results if r["check"] == "translation_verification"]
        assert trans_checks, "Should still emit a result even when source is unresolvable"
        r = trans_checks[0]
        assert r["status"] == "skipped", (
            f"Expected 'skipped' when source is unresolvable, got {r['status']!r}. "
            "A false 'warning' with inflated ratio is worse than an honest 'skipped'."
        )
