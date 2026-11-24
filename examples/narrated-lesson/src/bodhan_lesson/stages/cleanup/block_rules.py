"""Per-block cleanup rules (plan.v1.md rules 1, 4, 5, 6, 7, 7b, 9, 11; rule 2 was dropped, see below).

Text helpers are pure `str -> str` functions; the `apply_*` wrappers return a new
Block and record the rule in `rules_applied`. Rules 3, 8 and 10 need page context
and live in `page_rules.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace

from bodhan_lesson import config
from bodhan_lesson.models import Block

TEXT_TYPE = "Text"
EQUATION_TYPE = "Equation"

_MATH_SPAN = re.compile(r"\$\$[^$]+\$\$|\$[^$]+\$")
_PROSE_WORD = re.compile(r"\b[A-Za-z]*[a-z][A-Za-z]*\b")  # has a lowercase letter: prose, not variables
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")


def _tag(block: Block, rule: str, **changes: object) -> Block:
    return replace(block, rules_applied=[*block.rules_applied, rule], **changes)


def _prose_word_count(text: str) -> int:
    """Count prose words (3+ letters) outside math spans and LaTeX commands."""
    stripped = _LATEX_COMMAND.sub(" ", _MATH_SPAN.sub(" ", text))
    return sum(1 for word in _PROSE_WORD.findall(stripped) if len(word) >= 3)


# ---- Rule 11: mixed-block reclassification ----------------------------------
def prose_ratio(text: str) -> float:
    """Share of characters that belong to prose words rather than math."""
    stripped = _LATEX_COMMAND.sub(" ", _MATH_SPAN.sub(" ", text))
    prose_chars = sum(len(w) for w in _PROSE_WORD.findall(stripped) if len(w) >= 3)
    return prose_chars / max(len(text), 1)


def apply_reclassify_equation(block: Block) -> Block:
    if block.type != EQUATION_TYPE or prose_ratio(block.text) <= config.PROSE_RATIO_THRESHOLD:
        return block
    return _tag(block, "rule11_reclassify", type=TEXT_TYPE, reclassified=True)


# ---- Rule 6: canonical hyphenation ------------------------------------------
_HYPHEN_BREAK = re.compile(r"(\w+)-\n(\w+)")
_COMPOUND = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)+")


def build_vocab(blocks: list[Block]) -> set[str]:
    """Hyphenated compounds that appear intact on the page; used to decide hyphen vs. join."""
    return {m.group(0).lower() for b in blocks for m in _COMPOUND.finditer(b.text)}


def fix_hyphenation(text: str, vocab: set[str]) -> str:
    def rejoin(match: re.Match[str]) -> str:
        first, second = match.group(1), match.group(2)
        hyphenated = f"{first}-{second}"
        return hyphenated if hyphenated.lower() in vocab else first + second

    return _HYPHEN_BREAK.sub(rejoin, text)


def apply_hyphenation(block: Block, vocab: set[str]) -> Block:
    fixed = fix_hyphenation(block.text, vocab)
    return block if fixed == block.text else _tag(block, "rule6_hyphenation", text=fixed)


# ---- Rule 7 / 7b: whitespace, equation labels, answers, tables --------------
_WHITESPACE_RUN = re.compile(r"\s{3,}")
_TRAILING_EQ_LABEL = re.compile(r"\s*\((\d+)\)\s*$")
_TRAILING_ANSWER = re.compile(r"\s*\bAns\.?\s*$")


def apply_whitespace_and_labels(block: Block) -> Block:
    """Rule 7: collapse whitespace; move a trailing `(n)` and `Ans.` into metadata."""
    text = block.text
    if text.lstrip().startswith("|"):  # table markup: leave spacing alone
        return block
    changes: dict[str, object] = {}
    collapsed = _WHITESPACE_RUN.sub(" ", text)
    if block.type == TEXT_TYPE:
        if match := _TRAILING_EQ_LABEL.search(collapsed):
            changes["eq_label"] = match.group(1)
            collapsed = collapsed[: match.start()]
        if _TRAILING_ANSWER.search(collapsed):
            changes["is_answer"] = True
            collapsed = _TRAILING_ANSWER.sub("", collapsed)
    if collapsed == text and not changes:
        return block
    return _tag(block, "rule7_whitespace_labels", text=collapsed, **changes)


def apply_table_detection(block: Block) -> Block:
    """Rule 7b: a block whose text starts with a Markdown pipe is a table."""
    if block.type != "Table" and block.text.lstrip().startswith("|"):
        return _tag(block, "rule7b_table", type="Table")
    return block


# ---- Rule 4: example-number detection ---------------------------------------
_EXAMPLE_START = re.compile(r"^(\d+)\s+([A-Z])")


def apply_example_number(block: Block) -> Block:
    match = _EXAMPLE_START.match(block.text)
    if block.type != TEXT_TYPE or not match:
        return block
    text = f"**Example {match.group(1)}:** {block.text[match.end() - 1:]}"
    return _tag(block, "rule4_example", text=text, label="Example")


# ---- Rule 1: decimal point rendered as \cdot --------------------------------
_CDOT_DECIMAL = re.compile(r"(\d)\\cdot(\d)")


def fix_decimal_cdot(text: str) -> str:
    return _CDOT_DECIMAL.sub(r"\1.\2", text)


def apply_decimal_cdot(block: Block) -> Block:
    """Applied to Text blocks, and to Equation blocks only if they contain prose.

    A pure-math Equation block keeps `\\cdot` because there it means multiplication.
    """
    pure_math = block.type == EQUATION_TYPE and _prose_word_count(block.text) == 0
    if block.type not in (TEXT_TYPE, EQUATION_TYPE) or pure_math:
        return block
    fixed = fix_decimal_cdot(block.text)
    return block if fixed == block.text else _tag(block, "rule1_decimal_cdot", text=fixed)


# ---- Rule 9: angle-notation normalisation -----------------------------------
_ANGLE_LOOSE = re.compile(r"\$\\angle\$\s*([A-Z]{2,4})\b")
_ANGLE_MATHRM = re.compile(r"\\angle\s*\\mathrm\{([^}]*)\}")


def normalise_angle_notation(text: str) -> str:
    text = _ANGLE_LOOSE.sub(r"$\\angle \\text{\1}$", text)
    return _ANGLE_MATHRM.sub(r"\\angle \\text{\1}", text)


def apply_angle_normalisation(block: Block) -> Block:
    if block.type not in (TEXT_TYPE, EQUATION_TYPE):
        return block
    fixed = normalise_angle_notation(block.text)
    return block if fixed == block.text else _tag(block, "rule9_angle", text=fixed)


# ---- Rule 5: doubtful flagging ----------------------------------------------
# Rule 2 of plan.v1.md (`$word$` -> italic) was removed: it rewrote geometry labels
# such as `$XY$` into `*XY*`, destroying valid math.
DOUBTFUL_PATTERNS: dict[str, re.Pattern[str]] = {
    "garbled_overset_dot": re.compile(r"\\overset\s*\{\\dot\{"),
    "garbled_underset": re.compile(r"\\underset\{"),
    "empty_braces": re.compile(r"\{\s*\}"),
    "replacement_char": re.compile("\ufffd"),
    "repeated_char": re.compile(r"([^\s.\-_=*])\1{5,}"),
    # A block that is one unbroken math span mixing digits and letters (e.g. `$0962\mathrm{CH}06$`) is
    # a printed code (edge marks, serial numbers), not lesson content.
    "isolated_code_token": re.compile(
        r"^\s*\$(?=[^$]{6,}\$)(?=[^$]*\d)(?=[^$]*[A-Za-z])"
        r"(?:[A-Za-z0-9]|\\(?:mathrm|text)\{[A-Za-z0-9]+\})+\$\s*$"
    ),
}
_CHECKED_TYPES = frozenset({TEXT_TYPE, EQUATION_TYPE, "Title", "SectionHeader", "Caption"})
_ALLOWED_PUNCTUATION = set(".,;:!?'\"()[]-/%&+=<>*\u00b0")
JUNK_RATIO_THRESHOLD = 0.20
JUNK_MIN_LENGTH = 10


def junk_ratio(text: str) -> float:
    """Share of non-math characters that are neither letters, digits, spaces nor common punctuation."""
    prose = _MATH_SPAN.sub("", text)
    if len(prose) < JUNK_MIN_LENGTH:
        return 0.0
    junk = sum(1 for c in prose if not (c.isalnum() or c.isspace() or c in _ALLOWED_PUNCTUATION))
    return junk / len(prose)


def _latex_unbalanced(text: str) -> bool:
    depth = 0
    for char in text.replace(r"\{", "").replace(r"\}", ""):
        depth += (char == "{") - (char == "}")
        if depth < 0:
            return True
    return depth != 0 or text.replace(r"\$", "").count("$") % 2 == 1


def doubtful_reasons(block: Block) -> list[str]:
    """Text-quality signals. `conf` is detection confidence, not text accuracy, so a low
    value only counts when at least one text-quality signal agrees."""
    if block.type not in _CHECKED_TYPES or not block.text.strip():
        return []
    reasons = [name for name, pattern in DOUBTFUL_PATTERNS.items() if pattern.search(block.text)]
    if block.type in (TEXT_TYPE, EQUATION_TYPE) and len(block.text) < block.width * config.MIN_CHARS_PER_BBOX_PX:
        reasons.append("text_too_short_for_bbox")
    if _latex_unbalanced(block.text):
        reasons.append("latex_unbalanced")
    if junk_ratio(block.text) > JUNK_RATIO_THRESHOLD:
        reasons.append("junk_symbols")
    if reasons and block.conf < config.DOUBTFUL_CONF_THRESHOLD:
        reasons.append(f"low_conf={block.conf:.2f}")
    return reasons


def apply_doubtful_flag(block: Block) -> Block:
    reasons = doubtful_reasons(block)
    if not reasons:
        return block
    return _tag(block, "rule5_doubtful", doubtful=True, doubtful_reason=";".join(reasons))
