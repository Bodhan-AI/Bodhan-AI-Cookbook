"""indic-transliterate: POST /transliterate. One language; only the writing system changes."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.bodhan.capabilities import ensure_transliterate_language, ensure_transliterate_script
from app.services.bodhan.exceptions import BodhanError, BodhanProtocolError, BodhanRequestInvalid
from app.services.bodhan.translation import MAX_SPLIT_DEPTH
from app.utils.text import split_in_two, split_text

SERVICE = "transliterate"


@dataclass
class TransliterationSegment:
    index: int
    source_text: str
    converted_text: str
    separator: str = " "
    truncated: bool = False
    request_id: str | None = None


@dataclass
class TransliterationResult:
    language: str
    script: str
    converted_text: str
    segments: list[TransliterationSegment] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return any(s.truncated for s in self.segments)


async def _chunk(client, text: str, language: str, script: str, depth: int) -> list[TransliterationSegment]:
    try:
        resp = await client.request(SERVICE, "POST", "/transliterate",
                                    json_body={"text": text, "language": language, "script": script})
        body = resp.json
        if not isinstance(body, dict) or not isinstance(body.get("transliteration"), str):
            raise BodhanProtocolError(SERVICE, "Response did not contain a 'transliteration' string.",
                                      status_code=resp.status_code, request_id=resp.request_id)
        out, truncated = body["transliteration"], bool(body.get("truncated", False))
        rid = resp.request_id
    except BodhanError as exc:
        if exc.status_code == 404:
            chat_payload = {
                "model": "indic-transliterate",
                "messages": [{"role": "user", "content": text}],
                "language": language,
                "target_language": language,
                "script": script,
            }
            resp = await client.request(SERVICE, "POST", "/v1/chat/completions", json_body=chat_payload)
            body = resp.json
            choices = body.get("choices") if isinstance(body, dict) else None
            if not choices or not isinstance(choices, list) or "message" not in choices[0]:
                raise BodhanProtocolError(SERVICE, "Response did not contain choices[0].message.content.",
                                          status_code=resp.status_code, request_id=resp.request_id)
            out = choices[0]["message"].get("content", "")
            truncated = choices[0].get("finish_reason") == "length"
            rid = resp.request_id
        else:
            raise
    if truncated and depth < MAX_SPLIT_DEPTH:
        halves = split_in_two(text)
        if halves:
            return (await _chunk(client, halves[0], language, script, depth + 1)
                    + await _chunk(client, halves[1], language, script, depth + 1))
    return [TransliterationSegment(0, text, out, truncated=truncated, request_id=resp.request_id)]


async def transliterate_text(client, text: str, language: str, *, script: str = "roman",
                             max_chunk_chars: int = 2000) -> TransliterationResult:
    ensure_transliterate_language(language)
    ensure_transliterate_script(script)
    chunks = split_text(text, max_chunk_chars)
    if not chunks:
        raise BodhanRequestInvalid("There is no text to convert.", code="empty_text")
    result = TransliterationResult(language, script, "")
    pieces: list[str] = []
    for chunk in chunks:
        segs = await _chunk(client, chunk.text, language, script, 0)
        for i, seg in enumerate(segs):
            seg.index = len(result.segments)
            seg.separator = chunk.separator if i == len(segs) - 1 else " "
            result.segments.append(seg)
            pieces.append(seg.converted_text + seg.separator)
    result.converted_text = "".join(pieces).rstrip()
    return result
