"""Pedagogical narration planning and verbal script generator grounded in source blocks."""

from __future__ import annotations

import re

import httpx

from bodhan_lesson.cache import disk_cache
from bodhan_lesson.client import bodhan_client
from bodhan_lesson.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MOCK_MODE
from bodhan_lesson.schemas import BlockType, LessonSection, NarrationItem


def verbalize_equation_math(latex_str: str) -> str:
    """Deterministic converter converting LaTeX math into intuitive spoken English."""
    cleaned = latex_str.strip("$").strip()
    spoken = cleaned

    # 1. Strip non-spoken LaTeX metadata (tags, equation numbers, labels)
    spoken = re.sub(r"\\tag\{[^}]*\}", "", spoken)
    spoken = re.sub(r"\\eqno\([^)]*\)", "", spoken)
    spoken = re.sub(r"\\label\{[^}]*\}", "", spoken)

    # 2. Unwrap formatting wrappers
    spoken = re.sub(r"\\(?:mathbf|mathit|mathrm|textbf|textit|text)\{([^}]+)\}", r"\1", spoken)

    # 3. Common geometric, physical & algebraic vocalizations
    replacements = [
        # Geometric & symbols
        (r"\\angle\s*([A-Za-z0-9]+)", r"angle \1"),
        (r"\\Delta\s*([A-Za-z0-9]+)", r"triangle \1"),
        (r"\^\{?\\circ\}?", " degrees"),
        (r"\\circ", " degrees"),
        (r"\\parallel", " is parallel to "),
        (r"\\perp", " is perpendicular to "),
        (r"\\cong", " is congruent to "),
        (r"\\approx", " is approximately "),
        (r"\\neq", " is not equal to "),
        (r"\\le", " is less than or equal to "),
        (r"\\ge", " is greater than or equal to "),
        (r"\\ll", " is much less than "),
        (r"\\gg", " is much greater than "),
        (r">>", " is much greater than "),
        (r"<<", " is much less than "),
        (r"\\times", " multiplied by "),
        (r"\\div", " divided by "),
        (r"\\pm", " plus or minus "),
        (r"\\cdot", " dot "),
        (r"\\infty", " infinity "),
        (r"\\propto", " is proportional to "),
        (r"\\quad|\\qquad", " "),
        # Physics / Greek letters and symbols
        (r"\\mu_r", " relative permeability mu r "),
        (r"\\mu_0", " permeability mu zero "),
        (r"\\mu", " mu "),
        (r"\\chi_m", " magnetic susceptibility chi m "),
        (r"\\chi", " magnetic susceptibility chi "),
        (r"\\varepsilon|\\epsilon_0", " permittivity epsilon zero "),
        (r"\\varepsilon|\\epsilon", " epsilon "),
        (r"\\alpha", " alpha "),
        (r"\\beta", " beta "),
        (r"\\gamma", " gamma "),
        (r"\\delta", " delta "),
        (r"\\theta", " theta "),
        (r"\\lambda", " lambda "),
        (r"\\pi", " pi "),
        (r"\\rho", " rho "),
        (r"\\sigma", " sigma "),
        (r"\\tau", " tau "),
        (r"\\phi", " phi "),
        (r"\\omega", " omega "),
        # Fractions and roots
        (r"\\sqrt\{([^}]+)\}", r"square root of \1"),
        (r"\\frac\{([^}]+)\}\{([^}]+)\}", r"fraction \1 over \2"),
        # Subscripts & superscripts
        (r"_\{([^}]+)\}", r" sub \1 "),
        (r"_([0-9a-zA-Z])", r" sub \1 "),
        (r"\^\{([^}]+)\}", r" to the power of \1 "),
        (r"\^", " to the power of "),
        # Basic operators
        (r"=", " equals "),
        (r"\+", " plus "),
        (r"-", " minus "),
        (r"<", " is less than "),
        (r">", " is greater than "),
    ]
    for pattern, repl in replacements:
        spoken = re.sub(pattern, repl, spoken)

    # 4. Clean up any leftover braces or backslashes
    spoken = re.sub(r"[{}]", "", spoken)
    spoken = re.sub(r"\\[a-zA-Z]+", "", spoken)
    spoken = re.sub(r"\\", "", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken


def clean_spoken_text(text: str) -> str:
    """Sanitize arbitrary text into natural spoken words suitable for Indic TTS."""
    if not text:
        return ""

    # 1. Remove URLs (http://..., https://..., www....)
    text = re.sub(r"https?://\S+|www\.\S+", "", text)

    # 2. Convert inline LaTeX math: $...$ using verbalize_equation_math
    def replace_inline_math(match: re.Match) -> str:
        return verbalize_equation_math(match.group(0))

    text = re.sub(r"(?<!\\)\$[^$\n]+?\$", replace_inline_math, text)

    # 3. Strip markdown syntax (bold, italics, code, table rows, headers)
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"(?m)^\s*\|.*\|", "", text)
    text = text.replace("|", " ")

    # 4. Strip leftover LaTeX tags, metadata, and wrappers
    text = re.sub(r"\\tag\{[^}]*\}", "", text)
    text = re.sub(r"\\eqno\([^)]*\)", "", text)
    text = re.sub(r"\\label\{[^}]*\}", "", text)
    text = re.sub(r"\\(?:mathbf|mathit|mathrm|textbf|textit|text)\{([^}]+)\}", r"\1", text)

    # 5. Common comparison and math symbols in prose
    text = text.replace(">>", " is much greater than ")
    text = text.replace("<<", " is much less than ")
    text = text.replace(">=", " is greater than or equal to ")
    text = text.replace("<=", " is less than or equal to ")
    text = text.replace("%", " percent ")
    text = text.replace("&", " and ")

    # 6. Strip remaining LaTeX backslash commands and braces
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = text.replace("{", "").replace("}", "").replace("\\", "")

    # 7. Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


class NarrationPlanner:
    """Plans and generates grounded pedagogical narration scripts for textbook sections."""

    def __init__(self) -> None:
        self.client = bodhan_client
        self.llm_base_url = LLM_BASE_URL
        self.llm_model = LLM_MODEL
        self.llm_key = LLM_API_KEY

    def generate_narration(
        self,
        section: LessonSection,
        target_lang: str,
        source_lang: str = "en",
        code_mixed: bool = False,
        glossary_terms: list[str] | None = None,
        fast_narration: bool = False,
    ) -> NarrationItem:
        """Create a grounded pedagogical narration script in English, then translate to target_lang."""
        grounding_refs = [b.block_id for b in section.blocks]

        # Step 1: Draft pedagogical English script
        english_script = self._draft_english_narration(section, fast_narration=fast_narration)
        english_script = clean_spoken_text(english_script)

        # Step 2: Translate English narration script to target Indic language via Bodhan Indic-Translate
        if target_lang.lower() == "en":
            target_script = english_script
        else:
            if code_mixed and glossary_terms:
                from bodhan_lesson.translator import StructureShield

                shield = StructureShield()
                shielded = shield.shield(english_script, preserve_terms=glossary_terms)
                raw_translated = self.client.translate_text(
                    text=shielded,
                    target_lang=target_lang,
                    source_lang="en",
                )
                target_script, _ = shield.unshield(raw_translated)
            else:
                target_script = self.client.translate_text(
                    text=english_script,
                    target_lang=target_lang,
                    source_lang="en",
                )
            target_script = clean_spoken_text(target_script)

        return NarrationItem(
            section_id=section.section_id,
            section_slug=section.section_slug,
            section_title=section.title,
            english_script=english_script,
            target_script=target_script,
            target_language=target_lang,
            grounding_references=grounding_refs,
        )

    def _draft_english_narration(self, section: LessonSection, fast_narration: bool = False) -> str:
        """Draft English narration using LLM if available, with deterministic fallback."""
        if fast_narration:
            return self._generate_rule_based_narration(section)

        # Check if LLM call is viable
        if not MOCK_MODE and self.llm_key and "placeholder" not in self.llm_key.lower():
            try:
                script = self._call_llm_narration_planner(section)
                if script and len(script.strip()) > 30:
                    return script
            except Exception:
                pass  # Fall through to deterministic pedagogical verbalizer

        return self._generate_rule_based_narration(section)

    def _call_llm_narration_planner(self, section: LessonSection) -> str | None:
        """Query LLM to generate an educational tutor script strictly grounded in section text."""
        # Build context from blocks
        content_lines = []
        for b in section.blocks:
            if b.normalized_type == BlockType.PARAGRAPH:
                content_lines.append(f"[Text]: {b.text}")
            elif b.normalized_type == BlockType.EQUATION:
                content_lines.append(f"[Equation]: {b.text}")
            elif b.normalized_type == BlockType.FIGURE_CAPTION:
                content_lines.append(f"[Figure Caption]: {b.text}")
            elif b.normalized_type == BlockType.TABLE:
                content_lines.append(f"[Table]: {b.text}")

        context_prompt = "\n".join(content_lines)

        # Check disk cache first to eliminate duplicate LLM token charges
        cached_script = disk_cache.get_narration(section.title, context_prompt, self.llm_model)
        if cached_script:
            return cached_script

        headers = {
            "Authorization": f"Bearer {self.llm_key}",
            "Content-Type": "application/json",
        }

        system_prompt = (
            "You are an expert school educator narrating a textbook lesson. "
            "Your task is to write a warm, engaging, and clear spoken narration script in English. "
            "Rules:\n"
            "1. Grounding: Speak ONLY about concepts, equations, and diagrams explicitly present in the provided text.\n"
            "2. No Boilerplate: Do NOT add artificial welcome greetings or sign-offs (e.g. do not say 'Welcome to this "
            "section' or 'This concludes our discussion'). Start directly with the actual content.\n"
            "3. Repetitive Exercises: If the section contains long lists of exercise questions, summarize the "
            "problem-solving themes and walk through 1 or 2 sample questions concisely rather than reading every number.\n"
            "4. Spoken Math: Do NOT read raw LaTeX or symbols. Vocalize equations naturally (e.g. say 'angle A-O-B "
            "plus angle B-O-C equals 180 degrees').\n"
            "5. Visuals: Walk the student through figures based on their captions and text descriptions.\n"
            "6. Tone: Encouraging, clear, and pedagogical like a master teacher.\n"
            "7. Output ONLY the spoken narration text in plain English. No meta-commentary, no markdown headers."
        )

        user_prompt = f"Section: {section.title}\n\nContent:\n{context_prompt}\n\nPlease generate the spoken lesson narration."

        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1200,
        }

        with httpx.Client(timeout=35.0) as client:
            resp = client.post(f"{self.llm_base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            generated = data["choices"][0]["message"]["content"].strip()
            if generated:
                disk_cache.set_narration(section.title, context_prompt, self.llm_model, generated)
            return generated

    def _is_exercise_content(self, text: str) -> bool:
        """Check if a text block contains repetitive numerical drill questions or exercises."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) < 3:
            return False
        numbered_items = sum(1 for line in lines if re.match(r"^(\d+[\.\)]|\([a-z0-9]+\)|Q\d+[:.])", line, re.I))
        return numbered_items >= 3 or (numbered_items / len(lines) >= 0.5)

    def _generate_rule_based_narration(self, section: LessonSection) -> str:
        """Deterministic rule-based pedagogical script generation grounded in extracted blocks."""
        sentences: list[str] = []
        is_exercise_section = bool(
            re.search(r"\b(exercise|exercises|practice problems|questions|review questions)\b", section.title, re.I)
        )

        if is_exercise_section:
            sentences.append(f"In this section on {section.title}, we review the practice exercises and key problems.")

        for b in section.blocks:
            if b.normalized_type == BlockType.PARAGRAPH:
                clean_p = clean_spoken_text(b.text)
                if not clean_p or clean_p.startswith("#"):
                    continue

                if self._is_exercise_content(b.text):
                    # Concise grounded framing for repetitive drills instead of verbatim recitation
                    lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
                    q_samples = [
                        clean_spoken_text(ln)
                        for ln in lines
                        if re.match(r"^(\d+[\.\)]|\([a-z0-9]+\)|Q\d+[:.])", ln, re.I)
                    ][:2]
                    sample_str = " For example: " + " Next: ".join(q_samples) if q_samples else ""
                    sentences.append(
                        f"This set contains practice exercises to reinforce these principles.{sample_str} "
                        "Work through each problem using the formulas derived in this chapter."
                    )
                else:
                    sentences.append(clean_p)

            elif b.normalized_type == BlockType.EQUATION:
                spoken_eq = verbalize_equation_math(b.text)
                sentences.append(f"Here, we observe the key mathematical relationship: {spoken_eq}.")

            elif b.normalized_type == BlockType.FIGURE_CAPTION:
                clean_cap = clean_spoken_text(b.text)
                sentences.append(f"Referring to the illustration, {clean_cap}. Notice how the components align.")

            elif b.normalized_type == BlockType.TABLE:
                sentences.append("Examining the values summarized in the table, let us review each corresponding entry.")

        # Return strictly grounded text without artificial welcome or conclusion boilerplate
        narration_text = " ".join(sentences).strip()
        if not narration_text:
            narration_text = f"This section presents the principles of {section.title}."
        return narration_text


narration_planner = NarrationPlanner()
