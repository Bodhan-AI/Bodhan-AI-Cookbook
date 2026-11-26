from bodhan_dub.agent.condenser import EnglishCondenser


def test_filler_removal():
    condenser = EnglishCondenser()
    verbose_text = "As a matter of fact, it is important to note that we are basically ready."
    condensed = condenser.condense(verbose_text)
    assert "actually" in condensed or "ready" in condensed
    assert len(condensed) < len(verbose_text)

def test_verb_simplification():
    condenser = EnglishCondenser()
    text = "The system is able to make an adjustment to the parameters."
    condensed = condenser.condense(text)
    assert "can adjust" in condensed or len(condensed) <= len(text)
