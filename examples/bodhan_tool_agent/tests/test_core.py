"""Core plumbing: audio windows, ledger, client retries, language ID."""

from __future__ import annotations

import array
import math

import pytest

from bodhan_core import audio
from bodhan_core.errors import BudgetExceeded, ToolError
from bodhan_core.ledger import CostLedger
from bodhan_core.langid import contains_indic, detect_language, script_fraction


def _signal(seconds, rate=16000, quiet=()):
    n = int(seconds * rate)
    s = array.array("h", [int(8000 * math.sin(i / 7)) for i in range(n)])
    for t in quiet:
        for i in range(int(t * rate), min(int((t + 0.3) * rate), n)):
            s[i] = 0
    return s


def test_short_audio_is_one_window():
    assert audio.plan_windows(_signal(10), 16000, 25, 4) == [(0, 160000)]


def test_long_audio_windows_never_exceed_limit_and_cover_everything():
    s = _signal(95)
    wins = audio.plan_windows(s, 16000, 25, 4)
    assert wins[0][0] == 0 and wins[-1][1] == len(s)
    assert all(b - a <= 25 * 16000 for a, b in wins)
    assert all(wins[i][1] == wins[i + 1][0] for i in range(len(wins) - 1))  # contiguous: no audio dropped
    assert len(wins) >= 4


def test_cut_snaps_to_quiet_point():
    s = _signal(40, quiet=(23.0,))
    (a, b), _ = audio.plan_windows(s, 16000, 25, 4)
    assert 22.9 * 16000 <= b <= 23.4 * 16000


def test_ledger_blocks_before_exceeding_cap():
    led = CostLedger(max_inr=1.0)
    r = led.reserve(0.6)
    led.settle("m", r)
    with pytest.raises(BudgetExceeded):
        led.reserve(0.5)
    assert led.spent == pytest.approx(0.6)
    led.reserve(0.4)  # exactly the remainder is allowed


def test_ledger_release_frees_reservation():
    led = CostLedger(max_inr=1.0)
    r = led.reserve(0.9)
    led.release(r)
    led.reserve(0.9)


def test_client_retries_5xx_but_not_400(rt, fake):
    fake.fail_with["/translate"] = 500
    with pytest.raises(ToolError) as e:
        rt.client.post_json("indic-translate", "/translate", {})
    assert e.value.code == "upstream_error" and len(fake.calls) == 0  # failing path is answered before logging
    fake.fail_with["/translate"] = 400
    with pytest.raises(ToolError) as e:
        rt.client.post_json("indic-translate", "/translate", {})
    assert e.value.code == "bad_request" and not e.value.retryable


def test_missing_key_is_structured(rt):
    rt.settings.keys["indic-ocr"] = ""
    with pytest.raises(ToolError) as e:
        rt.client.post_json("indic-ocr", "/v1/chat/completions", {})
    assert e.value.code == "missing_api_key" and "BODHAN_OCR_KEY" in e.value.hint


def test_langid():
    assert detect_language("नमस्ते दुनिया")["language"] == "hi"
    assert detect_language("नमस्ते दुनिया")["ambiguous"] is True
    assert detect_language("வணக்கம் உலகம்")["language"] == "ta"
    assert detect_language("hello world")["language"] == "en"
    assert detect_language("")["language"] is None
    assert contains_indic("abc తెలుగు") and not contains_indic("plain English 123")
    assert script_fraction("hello", "Latin") == 1.0
