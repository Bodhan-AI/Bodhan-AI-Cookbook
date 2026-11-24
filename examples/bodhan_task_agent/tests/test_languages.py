import pytest

from bodhan_shared.languages import FEMALE_VOICES, infer_script_lang, normalize_lang


def test_supports_indic_names_codes_and_unsupported():
    assert normalize_lang("Tamil") == "ta"
    assert normalize_lang("ta") == "ta"
    assert FEMALE_VOICES["ta"] == "Anitha"
    with pytest.raises(ValueError):
        normalize_lang("French")


def test_language_inference_is_conservative():
    assert infer_script_lang("తెలుగు భాష") == "te"
    assert infer_script_lang("Hello") is None
