"""Stage 5a: describe each figure for a listener, using a vision-capable LLM.

The model sees only the figure crop, its caption and a little surrounding text; every word
of page text still comes from Indic-OCR. Descriptions go to `figures.json`, never into
`lesson.src.md`. Without a working vision model a figure falls back to its caption.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from bodhan_lesson import config
from bodhan_lesson.clients import llm
from bodhan_lesson.models import Element, FigureDescription
from bodhan_lesson.stages.assemble import Lesson
from bodhan_lesson.text import speech

log = logging.getLogger(__name__)

_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LABEL = re.compile(r"\b[A-Z]\b")
_WORDS_THAT_LOOK_LIKE_LABELS = frozenset({"A", "I"})  # the article and the pronoun

SYSTEM_PROMPT = """You describe educational diagrams for a student who cannot see them, so that a teacher can read the description aloud.

Rules:
- Use only what is visible in the image. Do not guess, and do not add measurements or labels that are not drawn.
- Name the labels exactly as drawn (points, rays, lines, angles, axes, objects) and say how they are arranged: which rays start from which point, which lines meet or are parallel, which parts are dashed, where arrows point.
- Write 1 to 3 plain English sentences that sound natural when spoken. Write letters separately and spaced, for example "ray O P", "angle A B C".
- Words only: never use symbols such as degree signs, equals signs, brackets or LaTeX.
- English only.
- If the image carries no lesson content (a logo, a border, decoration), set "decorative" to true.

Answer with one JSON object and nothing else:
{"description": "...", "decorative": false, "visible_labels": ["P", "Q"]}"""


def _figure_path(element: Element, source_dir: Path) -> Path | None:
    match = _LINK.search(element.markdown)
    return source_dir / match.group(1) if match else None


def _context(elements: list[Element], index: int) -> tuple[str, str]:
    """Plain text just before and just after the figure, for the model to read."""
    def nearest(candidates: list[Element]) -> str:
        for element in candidates:
            if element.kind in ("paragraph", "list", "equation", "heading", "note"):
                return speech.plain_speech(element.markdown)
        return ""

    before = nearest(list(reversed(elements[:index])))[-config.FIGURE_CONTEXT_CHARS:]
    after = nearest(elements[index + 1:])[:config.FIGURE_CONTEXT_CHARS]
    return before, after


def _user_prompt(caption: str, before: str, after: str) -> str:
    return (f"Caption: {caption or '(none)'}\nText before the figure: {before or '(none)'}\n"
            f"Text after the figure: {after or '(none)'}\n\nDescribe the figure in the image.")


def validate_description(data: dict, context: str) -> tuple[dict | None, list[str]]:
    """Check a vision answer. Returns (clean answer, problems); problems are fed back for a retry."""
    problems: list[str] = []
    description = data.get("description")
    decorative = bool(data.get("decorative", False))
    labels = [str(label) for label in data.get("visible_labels", []) if isinstance(label, (str, int))]
    if not isinstance(description, str):
        return None, ['"description" must be a string']
    description = " ".join(description.split())
    low, high = config.FIGURE_DESCRIPTION_CHARS
    if not decorative and not low <= len(description) <= high:
        problems.append(f"description must be {low}-{high} characters, not {len(description)}")
    if symbols := speech.forbidden_symbols(description):
        problems.append(f"description contains symbols that cannot be spoken: {' '.join(symbols)}")
    allowed = {label.upper() for label in labels} | set(_LABEL.findall(context))
    invented = sorted({m for m in _LABEL.findall(description) if m not in allowed} - _WORDS_THAT_LOOK_LIKE_LABELS)
    if invented:
        problems.append(f"description mentions labels not listed in visible_labels or the text: {' '.join(invented)}")
    return {"description": description, "decorative": decorative, "visible_labels": labels}, problems


def _describe_one(client: llm.LlmClient, vision_model: str, image: Path, caption: str, before: str, after: str,
                  state: dict) -> tuple[dict | None, list[str]]:
    messages = [llm.text_message("system", SYSTEM_PROMPT),
                llm.image_message(_user_prompt(caption, before, after), image, purpose="figure")]
    best: dict | None = None
    problems: list[str] = []
    for attempt in range(2):
        try:
            raw = client.complete(messages, model=vision_model, max_tokens=config.LLM_MAX_TOKENS_FIGURE)
            answer, problems = validate_description(llm.extract_json(raw), f"{caption} {before} {after}")
        except llm.LlmError as exc:
            if exc.status and 400 <= exc.status < 500 and exc.status != 429:
                state["vision_available"] = False  # the model rejects images: stop trying
            return best, [f"vision call failed: {exc}"]
        best = answer or best
        if not problems:
            return best, []
        messages += [llm.text_message("assistant", raw),
                     llm.text_message("user", "Fix these problems and answer again with the JSON only: " + "; ".join(problems))]
    return best, problems


def describe_figures(lesson: Lesson, source_dir: Path, client: llm.LlmClient | None) -> dict[str, FigureDescription]:
    """Describe every figure of the lesson; returns descriptions keyed by figure element id."""
    elements = lesson.elements
    vision_model = client and (client.settings.vision_model or client.settings.model)
    state = {"vision_available": bool(client)}

    def work(index: int) -> FigureDescription:
        element = elements[index]
        captions = [e for e in elements if e.paired_with == element.id]
        caption = " ".join(speech.plain_speech(c.markdown) for c in captions)
        path = _figure_path(element, source_dir)
        result = FigureDescription(element.id, element.block_ids, str(path.relative_to(source_dir)) if path else "", caption)
        if path is None or not path.is_file():
            result.warnings.append("figure image not found")
            return result
        with Image.open(path) as image:
            if min(image.size) < config.FIGURE_MIN_PIXELS:
                result.warnings.append("figure too small to describe")
                return result
        if not (client and vision_model and state["vision_available"]):
            result.warnings.append("no vision model available: caption only")
            return result
        before, after = _context(elements, index)
        answer, problems = _describe_one(client, vision_model, path, caption, before, after, state)
        if answer is None:
            result.warnings += problems or ["no usable description"]
            return result
        result.description, result.decorative = answer["description"], answer["decorative"]
        result.visible_labels, result.source = answer["visible_labels"], "vision"
        result.warnings += problems
        return result

    indexes = [i for i, e in enumerate(elements) if e.kind == "figure" and not e.doubtful]
    with ThreadPoolExecutor(max_workers=config.FIGURE_CONCURRENCY) as pool:
        results = list(pool.map(work, indexes))
    return {r.element_id: r for r in results}
