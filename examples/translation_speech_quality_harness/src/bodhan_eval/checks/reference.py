import unicodedata

from sacrebleu.metrics import BLEU, CHRF

_CHRF = CHRF(word_order=2)
_BLEU = BLEU(tokenize="none")


def score_reference(hypothesis: str | None, reference: str | None) -> dict[str, float | str] | None:
    if not hypothesis or not reference:
        return None
    if unicodedata.normalize("NFC", hypothesis) == unicodedata.normalize("NFC", reference):
        return {"reference_metric": "self_reference_skipped"}
    return {
        "chrf": round(_CHRF.sentence_score(hypothesis, [reference]).score, 6),
        "bleu": round(_BLEU.sentence_score(hypothesis, [reference]).score, 6),
    }
