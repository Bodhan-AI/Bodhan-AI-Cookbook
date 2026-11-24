"""
Unit tests for glossary loader and validation.
"""

from bodhan_dub.stages.glossary import Glossary, GlossaryBuilder


def test_glossary_term_lookup():
    user_terms = {
        "quantum computing": {"hi": "क्वांटम कंप्यूटिंग", "ta": "குவாண்டம் கம்ப்யூட்டிங்"},
        "iit madras": {"hi": "आईआईटी मद्रास"},
    }
    glossary = Glossary(user_terms=user_terms)

    text = "Students at IIT Madras research Quantum Computing daily."
    found = glossary.get_terms_for_text(text)
    assert "iit madras" in found
    assert "quantum computing" in found

    assert glossary.get_canonical("quantum computing", "hi") == "क्वांटम कंप्यूटिंग"
    assert glossary.get_canonical("quantum computing", "ta") == "குவாண்டம் கம்ப்யூட்டிங்"


def test_glossary_builder_user_file_loading(tmp_path):
    yaml_file = tmp_path / "glossary.yaml"
    yaml_file.write_text(
        """
terms:
  - source: "Neural Network"
    hi: "न्यूरल नेटवर्क"
""",
        encoding="utf-8",
    )

    builder = GlossaryBuilder(
        llm_client=None,
        bodhan_client=None,
        logger=None,
        user_glossary_path=yaml_file,
    )
    terms = builder.load_user_glossary()
    assert "neural network" in terms
    assert terms["neural network"]["hi"] == "न्यूरल नेटवर्क"
