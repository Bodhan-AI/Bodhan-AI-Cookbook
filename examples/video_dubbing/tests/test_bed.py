from __future__ import annotations

import numpy as np

from bodhan_dub import bed


def test_gate_silences_the_requested_span() -> None:
    rate = 1000
    gain = np.ones(1000, dtype=np.float32)

    bed._gate(gain, 0.3, 0.6, rate)

    assert gain[500] == 0.0
    assert gain[100] == 1.0
    assert gain[900] == 1.0


def test_gate_fades_instead_of_cutting() -> None:
    rate = 1000
    gain = np.ones(1000, dtype=np.float32)

    bed._gate(gain, 0.3, 0.6, rate)

    # Just before the mute begins the gain should be between 0 and 1.
    edge = gain[295]
    assert 0.0 < edge < 1.0


def test_gate_handles_overlapping_spans() -> None:
    rate = 1000
    gain = np.ones(1000, dtype=np.float32)

    bed._gate(gain, 0.2, 0.5, rate)
    bed._gate(gain, 0.4, 0.7, rate)

    assert gain[450] == 0.0
    assert gain[650] == 0.0
    assert gain[50] == 1.0


def test_gate_ignores_degenerate_spans() -> None:
    rate = 1000
    gain = np.ones(1000, dtype=np.float32)

    bed._gate(gain, 0.5, 0.5, rate)
    bed._gate(gain, -1.0, -0.5, rate)

    assert np.all(gain == 1.0)


def test_gate_clamps_to_the_buffer() -> None:
    rate = 1000
    gain = np.ones(100, dtype=np.float32)

    bed._gate(gain, -1.0, 10.0, rate)

    assert np.all(gain == 0.0)
