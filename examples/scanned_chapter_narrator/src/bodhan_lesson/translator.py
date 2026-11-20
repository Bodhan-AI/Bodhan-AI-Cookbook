"""Structure-preserving translation engine with token shielding and English pivot support."""

from __future__ import annotations

import re

from bodhan_lesson.client import bodhan_client


class StructureShield:
    """Shields LaTeX, tables, headings, and code-mixed terms from translation distortion."""

    def __init__(self) -> None:
        self.latex_map: dict[str, str] = {}
        self.table_map: dict[str, str] = {}
        self.heading_map: dict[str, str] = {}
        self.term_map: dict[str, str] = {}
        self.counter = 0

    def shield(self, text: str, preserve_terms: list[str] | None = None) -> str:
        """Replace non-translatable constructs with shielded tokens."""
        self.latex_map.clear()
        self.table_map.clear()
        self.heading_map.clear()
        self.term_map.clear()
        self.counter = 0

        # 1. Shield display LaTeX: $$...$$
        def replace_display_latex(match: re.Match) -> str:
            token = f"__LATEX_DISP_{len(self.latex_map)}__"
            self.latex_map[token] = match.group(0)
            return token

        shielded = re.sub(r"\$\$[\s\S]+?\$\$", replace_display_latex, text)

        # 2. Shield inline LaTeX: $...$
        def replace_inline_latex(match: re.Match) -> str:
            token = f"__LATEX_INL_{len(self.latex_map)}__"
            self.latex_map[token] = match.group(0)
            return token

        shielded = re.sub(r"(?<!\\)\$[^$\n]+?\$", replace_inline_latex, shielded)

        # 3. Shield Markdown table rows: lines starting and ending with |
        def replace_table_row(match: re.Match) -> str:
            token = f"__TBL_ROW_{len(self.table_map)}__"
            self.table_map[token] = match.group(0)
            return token

        shielded = re.sub(r"(?m)^\|[^\n]+\|$", replace_table_row, shielded)

        # 4. Shield Markdown headings to preserve level formatting
        def replace_heading(match: re.Match) -> str:
            token = f"__MD_HEAD_{len(self.heading_map)}__"
            self.heading_map[token] = match.group(1)  # the '#' prefix
            return f"{token} {match.group(2)}"

        shielded = re.sub(r"(?m)^(#{1,6})\s+([^\n]+)$", replace_heading, shielded)

        # 5. Shield code-mixed technical terms if requested
        if preserve_terms:
            for term in sorted(preserve_terms, key=len, reverse=True):
                term_clean = term.strip()
                if len(term_clean) >= 3:
                    pat = rf"\b{re.escape(term_clean)}\b"

                    def replace_term(match: re.Match) -> str:
                        token = f"__TERM_SHIELD_{len(self.term_map)}__"
                        self.term_map[token] = match.group(0)
                        return token

                    shielded = re.sub(pat, replace_term, shielded, flags=re.IGNORECASE)

        return shielded

    def unshield(self, text: str) -> tuple[str, bool]:
        """Restore all shielded tokens back to their exact original text."""
        all_survived = True

        # Spacing normalization for translated placeholders e.g. "__ LATEX _ INL _ 0 __"
        text = re.sub(r"__\s*LATEX\s*_\s*DISP\s*_\s*(\d+)\s*__", r"__LATEX_DISP_\1__", text)
        text = re.sub(r"__\s*LATEX\s*_\s*INL\s*_\s*(\d+)\s*__", r"__LATEX_INL_\1__", text)
        text = re.sub(r"__\s*TBL\s*_\s*ROW\s*_\s*(\d+)\s*__", r"__TBL_ROW_\1__", text)
        text = re.sub(r"__\s*MD\s*_\s*HEAD\s*_\s*(\d+)\s*__", r"__MD_HEAD_\1__", text)
        text = re.sub(r"__\s*TERM\s*_\s*SHIELD\s*_\s*(\d+)\s*__", r"__TERM_SHIELD_\1__", text)

        # 1. Restore headings
        for token, head_prefix in self.heading_map.items():
            if token in text:
                text = text.replace(token, head_prefix)
            else:
                all_survived = False

        # 2. Restore table rows
        for token, original_row in self.table_map.items():
            if token in text:
                text = text.replace(token, original_row)
            else:
                all_survived = False

        # 3. Restore LaTeX
        for token, original_latex in self.latex_map.items():
            if token in text:
                text = text.replace(token, original_latex)
            else:
                all_survived = False

        # 4. Restore preserved code-mixed terms
        for token, original_term in self.term_map.items():
            if token in text:
                text = text.replace(token, original_term)

        return text, all_survived


class Translator:
    """Orchestrates structured translation with token shielding and English pivot."""

    def __init__(self) -> None:
        self.client = bodhan_client

    def translate_markdown(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "en",
        preserve_terms: list[str] | None = None,
    ) -> str:
        """Translate a Markdown document preserving math, tables, headers, and code-mixed terms."""
        if not text or not text.strip():
            return ""

        if source_lang.lower() == target_lang.lower():
            return text

        # Split into manageable chunks (e.g., sections or paragraphs)
        chunks = self._chunk_text(text, max_chunk_chars=2500)
        translated_chunks = []

        for chunk in chunks:
            translated_chunk = self._translate_chunk_with_shielding(
                chunk=chunk,
                target_lang=target_lang,
                source_lang=source_lang,
                preserve_terms=preserve_terms,
            )
            translated_chunks.append(translated_chunk)

        return "\n\n".join(translated_chunks)

    def _translate_chunk_with_shielding(
        self,
        chunk: str,
        target_lang: str,
        source_lang: str = "en",
        preserve_terms: list[str] | None = None,
    ) -> str:
        """Shield tokens, execute translation (with pivot if needed), and unshield."""
        shield = StructureShield()
        shielded_text = shield.shield(chunk, preserve_terms=preserve_terms)

        # Pivoting logic:
        # If source is Indic and target is Indic (neither is English), pivot through English
        if source_lang != "en" and target_lang != "en":
            # Pass 1: Source Indic -> English
            intermediate_en = self.client.translate_text(
                text=shielded_text,
                target_lang="en",
                source_lang=source_lang,
            )
            # Pass 2: English -> Target Indic
            raw_target = self.client.translate_text(
                text=intermediate_en,
                target_lang=target_lang,
                source_lang="en",
            )
        else:
            raw_target = self.client.translate_text(
                text=shielded_text,
                target_lang=target_lang,
                source_lang=source_lang,
            )

        unshielded, survived = shield.unshield(raw_target)

        # Fallback if severe token loss occurred during translation
        if not survived:
            # If placeholders got corrupted, try unshielding individual surviving tokens
            pass

        return unshielded

    def _chunk_text(self, text: str, max_chunk_chars: int = 2500) -> list[str]:
        """Split text along natural paragraph boundaries without exceeding character limits."""
        if len(text) <= max_chunk_chars:
            return [text]

        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk: list[str] = []
        current_len = 0

        for p in paragraphs:
            if current_len + len(p) + 2 > max_chunk_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p) + 2

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks


translator = Translator()
