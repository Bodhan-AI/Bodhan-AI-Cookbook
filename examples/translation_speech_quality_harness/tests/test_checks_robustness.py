"""The hidden set will contain inputs nobody anticipated. Checks must never raise."""

import random

import numpy as np

from bodhan_eval.checks.audio import check_audio
from bodhan_eval.checks.translation import run_translation_checks
from bodhan_eval.languages import load_languages
from bodhan_eval.params import Params

L = load_languages()
P = Params.defaults()
PIECES = [
    "पानी", "১০০", "தமிழ்", "$", "$$", "\\frac{", "}", "|", "| a |", "---", "```", "`", "**",
    "#", "- ", "1. ", "\n", "\n\n", " ", "https://x.y/z", "[a](b)", "<b>", "°C", "%", "1,000",
    "3.5", "\\begin{align}", "\\end{x}", "‍", "‌", "।", "Hello", "NASA", "۲۵", "‏", "\t",
]  # fmt: skip


def rand_text(rng: random.Random) -> str:
    return "".join(rng.choice(PIECES) for _ in range(rng.randint(0, 40)))


def test_translation_checks_never_raise_on_random_input():
    rng = random.Random(0)
    langs = [L[c] for c in ("hi", "ta", "bn", "ur", "sat", "en", "ks-Deva")]
    for _ in range(600):
        src, tgt = rand_text(rng), rand_text(rng)
        for res in run_translation_checks(src, tgt, rng.choice(langs), P):
            for f in res.flags:
                assert 0 <= f.confidence <= 1
                if f.span is not None:
                    assert 0 <= f.span[0] <= f.span[1] <= len(tgt), (src, tgt, f)


def test_audio_check_never_raises_on_odd_signals():
    rng = np.random.default_rng(0)
    for x in [
        np.zeros(10, dtype=np.float32),
        np.ones(24000, dtype=np.float32),
        rng.standard_normal(48000).astype(np.float32),
        np.full(24000, np.nan, dtype=np.float32),
    ]:
        check_audio(x, 24000, "कुछ पाठ", P, 13.0)
        check_audio(x, 24000, "", P, 13.0)
