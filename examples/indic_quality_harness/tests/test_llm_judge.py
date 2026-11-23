import pytest
from bodhan_eval.llm_judge import JudgeResult
from pydantic import ValidationError


def test_valid_finding():
    result = JudgeResult.model_validate({
        "findings": [{
            "evidence_source": "translation",
            "category": "number_unit",
            "severity": "high",
            "span": "सुबह 8 बजे",
            "explanation": "Incorrect time.",

            "confidence": 0.96
        }]
    })

    assert len(result.findings) == 1


def test_clean_translation():
    result = JudgeResult.model_validate({"findings": []})
    assert result.findings == []


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        JudgeResult.model_validate({
            "findings": [{
                "category": "meaning error",
                "severity": "high",
                "span": "text",
                "explanation": "Error",
                "confidence": 0.9
            }]
        })


def test_invalid_confidence_rejected():
    with pytest.raises(ValidationError):
        JudgeResult.model_validate({
            "findings": [{
                "category": "omission",
                "severity": "high",
                "span": "text",
                "explanation": "Missing content",
                "confidence": 1.5
            }]
        })


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        JudgeResult.model_validate({
            "findings": [],
            "corrected_translation": "some text"
        })
