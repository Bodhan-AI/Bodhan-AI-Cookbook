"""Agentic reasoning engine: OCR block triage, margin note extraction, STEM glossary builder, and pedagogical narration planning."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from bodhan_lesson.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MOCK_MODE
from bodhan_lesson.narration import clean_spoken_text, verbalize_equation_math
from bodhan_lesson.schemas import BlockType, LessonSection, OCRBlock

logger = logging.getLogger("bodhan_lesson")


class ChapterNarratorAgent:
    """Autonomous LLM Agent coordinating block triage, glossary extraction, pedagogical planning, and QA diagnosis."""

    def __init__(self) -> None:
        self.llm_base_url = LLM_BASE_URL
        self.llm_model = LLM_MODEL
        self.llm_key = LLM_API_KEY

    @property
    def is_llm_available(self) -> bool:
        """Check if LLM endpoint credentials are configured and active."""
        return bool(not MOCK_MODE and self.llm_key and "placeholder" not in self.llm_key.lower())

    def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str | None:
        """Execute chat completion against configured OpenAI-compatible LLM endpoint."""
        if not self.is_llm_available:
            return None

        headers = {
            "Authorization": f"Bearer {self.llm_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{self.llm_base_url}/chat/completions", headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    return content
        except Exception as e:
            logger.debug(f"LLM Agent query failed: {e}. Using deterministic reasoning fallback.")
        return None

    # -------------------------------------------------------------------------
    # 1. Block Triage: Main Content vs. Margin Notes vs. Doubtful OCR
    # -------------------------------------------------------------------------
    def triage_blocks(
        self,
        blocks: list[OCRBlock],
    ) -> tuple[list[OCRBlock], list[dict[str, Any]]]:
        """Triage OCR blocks: isolate handwritten margin notes / callouts into annotations, and flag doubtful blocks."""
        main_blocks: list[OCRBlock] = []
        annotations: list[dict[str, Any]] = []

        margin_regex = re.compile(r"^(?:(?:handwritten|note|margin|tip|aside|nb):\s*|\*+\s*note:\s*)", re.IGNORECASE)

        for b in blocks:
            text_str = b.text.strip()
            if not text_str:
                continue

            # Check if block is a margin note or callout
            is_margin = False
            note_type = "margin_note"

            if margin_regex.search(text_str):
                is_margin = True
                note_type = "callout_note"
            elif b.bbox_xyxy and len(b.bbox_xyxy) == 4:
                # Geometric check: margin bounds (< 10% from left or > 90% right on normalized coords)
                x0, y0, x1, y1 = b.bbox_xyxy
                if x1 <= 1.0:
                    if x1 < 0.12 or x0 > 0.88:
                        is_margin = True
                else:  # Pixel coordinates for 1000px page
                    if x1 < 120 or x0 > 880:
                        is_margin = True

            # Check doubtful OCR
            if b.confidence is not None and b.confidence < 0.70:
                b.is_doubtful = True
                b.doubt_reason = f"Low OCR confidence ({b.confidence:.2f})"
            elif re.search(r"[^\x00-\x7F\u0900-\u0D7F]{4,}", text_str):
                b.is_doubtful = True
                b.doubt_reason = "Unrecognized character sequence"

            if is_margin:
                cleaned_note = margin_regex.sub("", text_str).strip()
                annotations.append({
                    "block_id": b.block_id,
                    "page_number": b.page_number,
                    "type": note_type,
                    "text": cleaned_note or text_str,
                    "bbox": b.bbox_xyxy,
                })
            else:
                main_blocks.append(b)

        return main_blocks, annotations

    # -------------------------------------------------------------------------
    # 2. Key STEM Glossary Extraction
    # -------------------------------------------------------------------------
    def extract_chapter_glossary(self, chapter_markdown: str) -> dict[str, str]:
        """Extract a structured glossary of 5 to 15 key STEM terms and definitions from chapter text."""
        # Try LLM Agent extraction
        system_prompt = (
            "You are a textbook curriculum specialist. Extract 5 to 12 key scientific, mathematical, "
            "or technical domain terms introduced in the chapter text. "
            "Output ONLY valid key: definition lines, e.g.:\n"
            "Diamagnetism: The property of materials that are weakly repelled by magnetic fields.\n"
            "Magnetic Susceptibility: The degree of magnetization of a material in response to an applied magnetic field."
        )
        user_prompt = f"Extract the key terms from this textbook text:\n\n{chapter_markdown[:3500]}"
        llm_resp = self._call_llm(system_prompt, user_prompt, max_tokens=600)

        glossary: dict[str, str] = {}
        if llm_resp:
            for line in llm_resp.split("\n"):
                line = line.strip().lstrip("-*0123456789. ")
                if ":" in line:
                    term, defn = line.split(":", 1)
                    term = term.strip().title()
                    defn = defn.strip()
                    if 2 <= len(term) <= 40 and len(defn) > 5:
                        glossary[term] = defn

        # Deterministic regex fallback if LLM returned insufficient terms
        if len(glossary) < 3:
            stem_patterns = [
                r"\b([A-Z][a-z]+(?:\s+[a-z]+)?)\s+(?:is defined as|is called|refers to|denotes)\s+([^.\n]+)",
                r"\b([A-Z][a-z]+(?:\s+[a-z]+)?)\s+is\s+a\s+([^.\n]+)",
            ]
            for pat in stem_patterns:
                for match in re.finditer(pat, chapter_markdown):
                    term = match.group(1).strip().title()
                    defn = match.group(2).strip()
                    if term not in glossary and len(term) > 3:
                        glossary[term] = defn
                        if len(glossary) >= 8:
                            break

        return glossary

    # -------------------------------------------------------------------------
    # 3. Pedagogical Narration Planning (Grounded in Blocks)
    # -------------------------------------------------------------------------
    def plan_narration(
        self,
        section: LessonSection,
        code_mixed: bool = False,
    ) -> str:
        """Synthesize a pedagogical spoken lecture script grounded strictly in section blocks."""
        # Build block context
        block_lines = []
        for b in section.blocks:
            if b.normalized_type == BlockType.PARAGRAPH:
                block_lines.append(f"[Paragraph]: {b.text}")
            elif b.normalized_type == BlockType.EQUATION:
                block_lines.append(f"[Equation]: {b.text}")
            elif b.normalized_type == BlockType.FIGURE_CAPTION:
                block_lines.append(f"[Figure]: {b.text}")
            elif b.normalized_type == BlockType.TABLE:
                block_lines.append(f"[Table]: {b.text}")

        context_prompt = "\n".join(block_lines)

        code_mixed_instruction = (
            "Keep technical STEM terms (like 'Magnetic Susceptibility', 'Curie Temperature', 'Permeability') "
            "in standard English terminology."
            if code_mixed
            else "Use natural accessible explanations."
        )

        system_prompt = (
            "You are a master teacher narrating a high school STEM lesson. "
            "Write an engaging, clear, and grounded spoken narration script in English. "
            "Rules:\n"
            "1. Grounding: Discuss ONLY concepts, formulas, and diagrams present in the provided text.\n"
            "2. Spoken Math: Vocalize all equations naturally (e.g. say 'fraction C over T' or 'angle AOB equals 180 degrees').\n"
            "3. Visuals: Explain figures clearly using their captions and surrounding text.\n"
            f"4. Terminology: {code_mixed_instruction}\n"
            "5. Output plain spoken narration text only. No bullet points or markdown headings."
        )

        user_prompt = f"Section: {section.title}\n\nContent:\n{context_prompt}\n\nPlease generate the spoken lesson narration."
        llm_resp = self._call_llm(system_prompt, user_prompt, max_tokens=1000)

        if llm_resp and len(llm_resp.strip()) > 40:
            return clean_spoken_text(llm_resp)

        # Deterministic Pedagogical Verbalizer Fallback
        return self._rule_based_narration(section, code_mixed)

    def _rule_based_narration(self, section: LessonSection, code_mixed: bool = False) -> str:
        """Deterministic pedagogical verbalization grounded in blocks."""
        sentences: list[str] = [f"Welcome to this section on {section.title}."]

        for b in section.blocks:
            if b.normalized_type == BlockType.PARAGRAPH:
                clean_p = clean_spoken_text(b.text)
                if clean_p and not clean_p.startswith("#"):
                    sentences.append(clean_p)

            elif b.normalized_type == BlockType.EQUATION:
                spoken_eq = verbalize_equation_math(b.text)
                sentences.append(f"Here, we observe the key mathematical relationship: {spoken_eq}.")

            elif b.normalized_type == BlockType.FIGURE_CAPTION:
                clean_cap = clean_spoken_text(b.text)
                sentences.append(f"Referring to the illustration, {clean_cap}. Notice how the components align.")

            elif b.normalized_type == BlockType.TABLE:
                sentences.append("Examining the values summarized in the table, let us review each corresponding entry.")

        sentences.append(f"This concludes our discussion on {section.title}.")
        return " ".join(sentences)

    # -------------------------------------------------------------------------
    # 4. QA Audio Verification Diagnosis & Autonomous Repair
    # -------------------------------------------------------------------------
    def diagnose_qa_result(
        self,
        coverage: float,
        cer: float,
        wer: float,
        repetition: bool,
    ) -> str:
        """Provide diagnostic evaluation of ASR back-transcription QA results."""
        diagnostics = []
        if repetition:
            diagnostics.append("TTS vocoder repetition loop flagged.")
        if coverage < 0.85:
            diagnostics.append(f"Coverage low ({coverage:.1%}); potential omission of script phrases.")
        if cer > 0.35:
            diagnostics.append(f"High character error rate ({cer:.2f}) observed in back-transcription.")
        if not diagnostics:
            return "Audio QA passed all quality targets."
        return " | ".join(diagnostics)

    def plan_audio_repair(
        self,
        script_text: str,
        qa_result: Any,
    ) -> str:
        """Autonomously repair script text based on QA failure diagnosis to resolve TTS loops or mis-transcription."""
        repaired = script_text
        repetition_detected = getattr(qa_result, "repetition_detected", False)
        repetition_phrases = getattr(qa_result, "repetition_phrases", [])
        cer = getattr(qa_result, "cer", 0.0)

        # 1. If runaway repetition loops detected: strip duplicate phrase loops
        if repetition_detected:
            # Clean reported repetition phrases
            for phrase in repetition_phrases:
                p_clean = phrase.strip()
                if p_clean:
                    words = p_clean.split()
                    if len(words) >= 2 and words[: len(words) // 2] == words[len(words) // 2 :]:
                        p_sub = " ".join(words[: len(words) // 2])
                        pat = re.compile(rf"\b({re.escape(p_sub)})(?:\s+\1)+\b", re.IGNORECASE)
                        repaired = pat.sub(r"\1", repaired)
                    pat = re.compile(rf"\b({re.escape(p_clean)})(?:\s+\1)+\b", re.IGNORECASE)
                    repaired = pat.sub(r"\1", repaired)

            # Clean any general consecutive 1-to-4 word phrase loops (e.g. "word word word")
            pat_general = re.compile(r"\b(\w+(?:\s+\w+){0,3})(?:\s+\1)+\b", re.IGNORECASE)
            repaired = pat_general.sub(r"\1", repaired)

        # 2. If high CER: sanitize any lingering LaTeX, control characters, or awkward punctuation that trips TTS vocoders
        if cer > 0.40:
            repaired = clean_spoken_text(repaired)
            repaired = re.sub(r"[\u2018\u2019]", "'", repaired)
            repaired = re.sub(r"[\u201C\u201D]", '"', repaired)
            repaired = re.sub(r"[\u2013\u2014]", ", ", repaired)
            repaired = re.sub(r"\s+", " ", repaired).strip()

        return repaired


chapter_narrator_agent = ChapterNarratorAgent()
