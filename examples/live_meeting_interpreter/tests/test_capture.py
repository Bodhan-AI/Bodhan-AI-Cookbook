from app.audio.capture import is_virtual_cable


def test_is_virtual_cable():
    assert is_virtual_cable("CABLE Input (VB-Audio Virtual Cable)")
    assert is_virtual_cable("CABLE Output (VB-Audio Virtual Cable)")
    assert is_virtual_cable("CABLE In 16ch (VB-Audio Virtual Cable)")
    assert not is_virtual_cable("Speakers (Realtek(R) Audio)")
    assert not is_virtual_cable("Microphone Array (Realtek(R) Audio)")
    assert not is_virtual_cable("AI Noise-cancelling Input (ASUS Utility)")
