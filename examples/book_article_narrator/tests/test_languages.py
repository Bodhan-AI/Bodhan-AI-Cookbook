from narrator import config

EXPECTED = {
    "hi", "bn", "mr", "ta", "te", "gu", "kn", "ml", "pa", "or", "as", "ur",
    "ne", "sa", "kok", "mai", "doi", "brx", "sat", "mni", "ks", "sd", "en",
}


def test_all_bodhan_languages_are_listed():
    assert set(config.LANGUAGES) == EXPECTED


def test_every_language_has_at_least_one_voice():
    assert set(config.VOICES) == set(config.LANGUAGES)
    for code, voices in config.VOICES.items():
        assert voices, code