"""Glossary building for terminology consistency.

The LLM extracts terms to keep consistent (names, acronyms, numbers, technical
words) from the English transcript; an optional --glossary YAML is merged on top
and wins. We honour the ground rules strictly: the glossary only steers the
ENGLISH side and the condense prompt — it never rewrites Indic text. Per-language
canonical renderings are produced by Indic-Translate (cached) purely for the
run report, so reviewers can see how each key term was translated.
"""

from __future__ import annotations

import yaml

from ..logging_setup import get_logger

log = get_logger("glossary")


class Glossary:
    def __init__(self, terms: dict[str, str]):
        self.terms = terms  # english term -> canonical english form (verbatim target)

    def as_prompt_hint(self) -> str:
        if not self.terms:
            return ""
        joined = ", ".join(sorted(self.terms))
        return f"Keep these terms consistent and do not drop them: {joined}."

    async def canonical_targets(self, api, target_token: str, script: str) -> dict[str, str]:
        """Bodhan-produced canonical translation of each term (report-only)."""
        out: dict[str, str] = {}
        for term in sorted(self.terms):
            try:
                tr, _ = await api.translate(term, target_token, script)
                out[term] = tr
            except Exception:
                continue
        return out


async def build_glossary(brain, transcript_text: str, override_path: str | None) -> Glossary:
    terms = await brain.build_glossary(transcript_text)
    if override_path:
        with open(override_path) as f:
            user = yaml.safe_load(f) or {}
        # Accept either {term: keep} or {terms: [..]} or a plain list.
        items = user.get("terms", user) if isinstance(user, dict) else user
        if isinstance(items, (dict, list)):
            terms.update({str(k): str(k) for k in items})
    log.info("built glossary", extra={"event": "glossary",
             "extra": {"n_terms": len(terms)}})
    return Glossary(terms)
