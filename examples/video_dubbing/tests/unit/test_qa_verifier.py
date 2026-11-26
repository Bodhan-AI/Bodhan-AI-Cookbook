from bodhan_dub.agent.qa_verifier import QualityAssuranceVerifier
from bodhan_dub.models.segment import DubSegment


def test_unicode_normalization():
    verifier = QualityAssuranceVerifier()
    # Combining characters and punctuations
    raw_indic = "नमस्ते!   यह ஒரு சோதனை।..."
    norm = verifier.normalize_indic_text(raw_indic)
    assert "!" not in norm
    assert "..." not in norm
    assert "नमस्ते" in norm
    assert "ஒரு" in norm

def test_levenshtein_distance():
    verifier = QualityAssuranceVerifier()
    assert verifier.calculate_levenshtein("kitten", "sitting") == 3
    assert verifier.calculate_levenshtein("भारत", "भारत") == 0
    assert verifier.calculate_levenshtein("भारत", "भारती") == 1

def test_cer_calculation():
    verifier = QualityAssuranceVerifier()
    # Exact match -> CER = 0
    ref = "यह एक उदाहरण वाक्य है"
    assert verifier.calculate_cer(ref, ref) == 0.0

    # Slight difference
    hyp = "यह एक उदाहरण वाक्य"
    cer = verifier.calculate_cer(ref, hyp)
    assert 0.0 < cer < 0.20

def test_verify_segment_acceptance(tmp_path):
    verifier = QualityAssuranceVerifier()
    seg = DubSegment(
        id="seg_001",
        start=0.0,
        end=2.0,
        source_text="Hello world",
        translated_text="नमस्ते दुनिया",
        applied_tempo_factor=1.05
    )

    # Create valid dummy wav
    wav_path = tmp_path / "valid.wav"
    import wave
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        # Non-silent valid samples
        wf.writeframes(b"\x10\x20" * 16000)

    verified = verifier.verify_segment(
        segment=seg,
        audio_path=wav_path,
        back_transcription="नमस्ते दुनिया"
    )
    assert verified.qa_status == "ACCEPTED"
    assert verified.cer == 0.0
