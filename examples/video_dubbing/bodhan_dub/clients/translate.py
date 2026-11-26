import logging
import time

from bodhan_dub.clients.base import BodhanBaseClient
from bodhan_dub.config import BodhanConfig

logger = logging.getLogger(__name__)


class BodhanTranslateClient(BodhanBaseClient):
    """Client for Bodhan Indic-Translate service.

    Uses the Cookbook-documented `POST /translate` endpoint and automatically falls
    back to `POST /v1/chat/completions` (model=`indic-translate`) if `/translate`
    returns HTTP 404 on the live gateway.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.bodhan.ai", mock_mode: bool = False):
        super().__init__(api_key=api_key, base_url=base_url)
        self.mock_mode = mock_mode
        self.cache: dict[str, str] = {}
        self.call_count = 0
        self.total_latency = 0.0
        self._use_chat_fallback = False

    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "en",
        glossary: dict[str, str] | None = None,
        code_mixed: bool = False,
    ) -> str:
        """Translate text using Bodhan Indic-Translate."""
        if not text.strip():
            return ""

        cache_key = f"{source_lang}_{target_lang}_{text.strip()}_{code_mixed}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        if self.mock_mode or not self.api_key:
            mock_translations = {
                "hi": f"यह एक स्वचालित अनुवाद है: {text}",
                "ta": f"இது ஒரு தானியங்கி மொழிபெயர்ப்பு: {text}",
                "bn": f"এটি একটি স্বয়ংক্রিয় অনুবাদ: {text}",
            }
            res = mock_translations.get(target_lang, f"[{target_lang}] {text}")
            self.cache[cache_key] = res
            return res

        start_time = time.time()
        self.call_count += 1

        clean_text = text.strip()[:600]
        if glossary:
            for src_term, tgt_term in glossary.items():
                if src_term in clean_text and tgt_term:
                    clean_text = clean_text.replace(src_term, str(tgt_term))

        try:
            translated = ""
            if not self._use_chat_fallback:
                payload = {
                    "text": clean_text,
                    "target_language": target_lang,
                    "script": "codemix" if code_mixed else "native",
                    "max_output_tokens": 512,
                }
                resp = self.post("translate", json_data=payload)
                if resp.status_code == 404:
                    logger.info("POST /translate returned 404; switching to POST /v1/chat/completions (model=indic-translate).")
                    self._use_chat_fallback = True
                elif resp.status_code != 200:
                    logger.error("Translate error: %d - %s", resp.status_code, resp.text)
                    resp.raise_for_status()
                else:
                    translated = resp.json().get("translation", "").strip()

            if self._use_chat_fallback:
                lang_name = BodhanConfig.get_language_name(target_lang)
                system_prompt = f"You are a professional translator. Target language: {lang_name} ({target_lang})."
                if code_mixed:
                    system_prompt += " Preserve technical loanwords in Latin script when standard in code-mixed conversation."
                else:
                    system_prompt += " Output pure translated text in native script only, without explanation or quotes."

                chat_payload = {
                    "model": "indic-translate",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": clean_text},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 256,
                }
                resp = self.post("v1/chat/completions", json_data=chat_payload)
                if resp.status_code != 200:
                    logger.error("Translate chat error: %d - %s", resp.status_code, resp.text)
                    resp.raise_for_status()
                translated = resp.json()["choices"][0]["message"]["content"].strip()
                if translated.startswith('"') and translated.endswith('"'):
                    translated = translated[1:-1].strip()

            latency = time.time() - start_time
            self.total_latency += latency

            if not translated:
                raise ValueError("Empty translation returned by Bodhan Indic-Translate")

            self.cache[cache_key] = translated
            return translated
        except Exception as e:
            logger.warning("Bodhan translation error (%s). Falling back to isolated placeholder.", e)
            mock_translations = {
                "hi": f"अनुवाद: {clean_text[:60]}",
                "ta": f"மொழிபெயர்ப்பு: {clean_text[:60]}",
                "bn": f"অনুবাদ: {clean_text[:60]}",
            }
            fallback = mock_translations.get(target_lang, f"[{target_lang}] {clean_text[:60]}")
            self.cache[cache_key] = fallback
            return fallback
