from bodhan_rules import allowed_models, check_rules, deprecated_models, load_rules


def test_rules_file_is_consistent():
    rules = load_rules()
    assert check_rules(rules) == []


def test_current_models_are_present():
    rules = load_rules()
    assert {"indic-transcribe", "indic-translate", "indic-transliterate", "indic-speak", "indic-ocr"} <= allowed_models(rules)


def test_deprecated_ids_map_to_current_ids():
    rules = load_rules()
    for old, new in deprecated_models(rules).items():
        assert old.startswith("bodhan-")
        assert new in rules["models"]


def test_check_rules_reports_bad_language_code():
    rules = load_rules()
    rules["models"]["indic-translate"]["languages"].append("zz")
    assert any("zz" in p for p in check_rules(rules))


def test_check_rules_rejects_upper_case_model_id():
    rules = load_rules()
    rules["models"]["Indic-Foo"] = dict(rules["models"]["indic-ocr"])
    assert any("lower-case" in p for p in check_rules(rules))
