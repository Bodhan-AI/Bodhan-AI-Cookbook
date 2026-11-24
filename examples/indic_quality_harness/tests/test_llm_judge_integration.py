"""Live test against a local Ollama. Skipped when Ollama is not running."""

import pytest
import requests
from bodhan_eval.llm_judge import DEFAULT_BASE_URL, DEFAULT_MODEL, OllamaJudge


def _ollama_ready() -> bool:
    try:
        response = requests.get(f"{DEFAULT_BASE_URL}/api/tags", timeout=2)
        names = {m.get("name") for m in response.json().get("models", [])}
        return DEFAULT_MODEL in names
    except (requests.RequestException, ValueError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _ollama_ready(), reason=f"Ollama with {DEFAULT_MODEL} not available"),
]


def test_ollama_detects_number_error():
    judge = OllamaJudge()

    result = judge.evaluate(
        source_text="The train will arrive at 6 PM.",
        translated_text="ट्रेन सुबह 8 बजे पहुंचेगी।",
        language="Hindi",
    )

    assert result.findings
    assert any(
        finding.category.value in {"number_unit", "mistranslation"}
        for finding in result.findings
    )

    for finding in result.findings:
        assert finding.span in "ट्रेन सुबह 8 बजे पहुंचेगी।"
