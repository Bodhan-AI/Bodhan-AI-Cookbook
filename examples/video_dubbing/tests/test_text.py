"""Regression checks for text-only source timing and protected English content."""

from bodhan_dub.text import safe_condensation, source_parts


def test_punctuation_free_transcript_keeps_sentence_fragments_together():
    source = (
        "In this lesson we will explain how the system works and which parts you will see in the diagram "
        "From your previous class you already know three different ways to identify the circuit "
        "An open circuit has a gap that stops current from flowing and a closed circuit has no gap "
        "This lesson will show how both circuits can be built using a simple switch"
    )
    parts = source_parts(source)
    assert len(parts) > 1
    assert " ".join(parts) == source
    assert all(len(part) >= 40 for part in parts)
    assert parts[1].startswith(("An open circuit", "This lesson"))


def test_condensation_preserves_spelled_numbers_and_relations():
    source = "The length of the side that is drawn in the diagram is less than five centimetres."
    assert safe_condensation(source, "The length is less than five centimetres.", [])
    assert not safe_condensation(source, "The length is less than nine centimetres.", [])
    assert not safe_condensation(source, "The length is greater than five centimetres.", [])
