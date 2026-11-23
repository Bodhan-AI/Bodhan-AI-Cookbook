"""Error injector: plants known errors in clean outputs, with labels, so the harness can be measured.

    python -m bodhan_eval.inject --dataset clean.jsonl --out injected.jsonl --rate 0.5 --seed 7

Modules:
    core     the method table (category, severity, what each method needs), the per-item choice, the command
    text     programmatic edits of the translation: delete, duplicate, swap, break markup, leave English
    english  ``--with-models``: change the English, re-translate one sentence with Indic-Translate, splice it in
    speech   edits of the waveform: cut, repeat, silence, noise, clipping; labelled through the speech time map
    base     the ``Edit`` a method returns, the shared context, text helpers

The injector never holds or calls an LLM client (CLAUDE.md §4.4; tested). Indian-language text it adds comes
from the dataset itself or from Bodhan models, never from an LLM.
"""
