"""
Glossary builder and terminology consistency manager for bodhan_dub.
Merges user glossary with LLM entity extraction and obtains canonical translations
via Indic-Translate.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml
from pydantic import BaseModel, Field

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.llm import LLMClient
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.stages.segment import SegmentSlot


class ExtractedEntities(BaseModel):
    terms: List[str] = Field(
        default_factory=list,
        description="List of extracted named entities, technical terms, acronyms, and units.",
    )


class Glossary:
    """Manages glossary terms and canonical translations per language."""

    def __init__(
        self,
        user_terms: Optional[Dict[str, Dict[str, str]]] = None,
        extracted_terms: Optional[Set[str]] = None,
    ):
        # term.lower() -> {lang_code: canonical_translation}
        self.user_terms = user_terms or {}
        self.extracted_terms = extracted_terms or set()
        # canonical_map: lang_code -> {term.lower(): translation}
        self.canonical_map: Dict[str, Dict[str, str]] = {}

    def add_term(self, term: str, translations: Optional[Dict[str, str]] = None) -> None:
        """Adds a term with optional predefined translations."""
        key = term.lower().strip()
        if translations:
            self.user_terms[key] = translations
        else:
            self.extracted_terms.add(key)

    def get_terms_for_text(self, text: str) -> List[str]:
        """Returns all glossary terms that appear in text (case-insensitive)."""
        all_terms = set(self.user_terms.keys()) | self.extracted_terms
        matching: List[str] = []
        for term in all_terms:
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            if pattern.search(text):
                matching.append(term)
        return matching

    def get_canonical(self, term: str, lang: str) -> str:
        """Returns canonical translation for term in given language."""
        key = term.lower().strip()
        # 1. User specified translation
        if key in self.user_terms and lang in self.user_terms[key]:
            return self.user_terms[key][lang]
        # 2. Cached model translation
        if lang in self.canonical_map and key in self.canonical_map[lang]:
            return self.canonical_map[lang][key]
        return term


class GlossaryBuilder:
    """Extracts, validates, and builds canonical glossaries."""

    def __init__(
        self,
        llm_client: LLMClient,
        bodhan_client: BodhanClient,
        logger: PipelineLogger,
        user_glossary_path: Optional[Path] = None,
    ):
        self.llm_client = llm_client
        self.bodhan_client = bodhan_client
        self.logger = logger
        self.user_glossary_path = user_glossary_path

    def load_user_glossary(self) -> Dict[str, Dict[str, str]]:
        """Loads user-supplied glossary YAML file."""
        if not self.user_glossary_path or not Path(self.user_glossary_path).exists():
            return {}

        try:
            with open(self.user_glossary_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            terms_dict: Dict[str, Dict[str, str]] = {}
            # Format: terms: [{source: "...", hi: "...", ta: "..."}, ...]
            for item in data.get("terms", []):
                src = item.get("source", "").strip().lower()
                if not src:
                    continue
                terms_dict[src] = {k: v for k, v in item.items() if k != "source"}
            return terms_dict
        except Exception as e:
            self.logger.warning(f"Failed to load user glossary '{self.user_glossary_path}': {e}", stage="glossary")
            return {}

    async def extract_terms_with_llm(self, segments: List[SegmentSlot]) -> Set[str]:
        """
        Uses LLM to extract names, acronyms, and technical terms from English transcript.
        Validates deterministically that every extracted term exists in transcript (Decision Point #7).
        """
        if not segments or not self.llm_client.settings.llm_api_key:
            return set()

        full_transcript = " ".join(s.text.strip() for s in segments)
        terms = await self.llm_client.extract_glossary_terms(full_transcript, stage="glossary")
        return set(t.lower() for t in terms)

    async def build(
        self,
        segments: List[SegmentSlot],
        target_langs: List[str],
    ) -> Glossary:
        """
        Builds full glossary and establishes canonical translations per target language.
        """
        user_terms = self.load_user_glossary()
        extracted_terms = await self.extract_terms_with_llm(segments)

        all_unique_terms = set(user_terms.keys()) | extracted_terms
        self.logger.info(
            f"Glossary initialized: {len(user_terms)} user terms, {len(extracted_terms)} extracted terms",
            stage="glossary",
        )

        glossary = Glossary(user_terms=user_terms, extracted_terms=extracted_terms)

        # Pre-translate isolated terms once per language to establish canonical translation
        for lang in target_langs:
            glossary.canonical_map[lang] = {}
            for term in all_unique_terms:
                # If user provided a translation, use that
                if term in user_terms and lang in user_terms[term]:
                    glossary.canonical_map[lang][term] = user_terms[term][lang]
                    continue

                # Otherwise call Indic-Translate once on the isolated term (cached)
                try:
                    translated, _ = await self.bodhan_client.translate(
                        text=term,
                        target_language_code=lang,
                        source_language_code="en",
                        stage="glossary",
                        lang=lang,
                    )
                    glossary.canonical_map[lang][term] = translated.strip()
                except Exception as e:
                    self.logger.warning(
                        f"Could not pre-translate glossary term '{term}' to {lang}: {e}",
                        stage="glossary",
                        lang=lang,
                    )
                    glossary.canonical_map[lang][term] = term

        return glossary

