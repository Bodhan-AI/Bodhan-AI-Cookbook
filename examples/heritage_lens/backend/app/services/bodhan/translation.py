"""indic-translate: POST /translate (JSON). No source-language field exists."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.bodhan.capabilities import ensure_translate_script, ensure_translate_target
from app.services.bodhan.exceptions import BodhanError, BodhanProtocolError, BodhanRequestInvalid
from app.utils.text import split_in_two, split_text

SERVICE = "translate"
MAX_SPLIT_DEPTH = 2


@dataclass
class TranslationSegment:
    index: int
    source_text: str
    translated_text: str
    separator: str = " "
    truncated: bool = False
    retried_split: bool = False
    request_id: str | None = None


@dataclass
class TranslationResult:
    target_language: str
    script: str
    translation: str
    segments: list[TranslationSegment] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def truncated(self) -> bool:
        return any(s.truncated for s in self.segments)


async def _call(client, text: str, target: str, script: str) -> tuple[str, bool, str | None, dict]:
    payload = {"text": text, "target_language": target}
    if script != "native":  # 'native' is the upstream default; only send what differs
        payload["script"] = script
    try:
        resp = await client.request(SERVICE, "POST", "/translate", json_body=payload)
        body = resp.json
        if not isinstance(body, dict) or not isinstance(body.get("translation"), str):
            raise BodhanProtocolError(SERVICE, "Response did not contain a 'translation' string.",
                                      status_code=resp.status_code, request_id=resp.request_id)
        return body["translation"], bool(body.get("truncated", False)), resp.request_id, body.get("usage") or {}
    except BodhanError as exc:
        if exc.status_code == 404:
            # Fallback to chat completions transport documented by Bodhan
            chat_payload = {
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "target_language": target,
            }
            if script != "native":
                chat_payload["script"] = script
            resp = await client.request(SERVICE, "POST", "/v1/chat/completions", json_body=chat_payload)
            body = resp.json
            choices = body.get("choices") if isinstance(body, dict) else None
            if not choices or not isinstance(choices, list) or "message" not in choices[0]:
                raise BodhanProtocolError(SERVICE, "Response did not contain choices[0].message.content.",
                                          status_code=resp.status_code, request_id=resp.request_id)
            content = choices[0]["message"].get("content", "")
            truncated = choices[0].get("finish_reason") == "length"
            usage = body.get("usage") or {}
            return content, truncated, resp.request_id, usage
        raise


async def _translate_chunk(client, text: str, target: str, script: str, depth: int,
                           usage: dict) -> list[TranslationSegment]:
    out, truncated, rid, u = await _call(client, text, target, script)
    usage["prompt"] += int(u.get("prompt_tokens", 0) or 0)
    usage["completion"] += int(u.get("completion_tokens", 0) or 0)
    if not truncated:
        return [TranslationSegment(0, text, out, request_id=rid)]
    halves = split_in_two(text) if depth < MAX_SPLIT_DEPTH else None
    if halves is None:  # cannot split further: return what we have, loudly flagged
        return [TranslationSegment(0, text, out, truncated=True, request_id=rid)]
    left = await _translate_chunk(client, halves[0], target, script, depth + 1, usage)
    right = await _translate_chunk(client, halves[1], target, script, depth + 1, usage)
    for seg in left + right:
        seg.retried_split = True
    return left + right


async def translate_text(client, text: str, target_language: str, *, script: str = "native",
                         max_chunk_chars: int = 2000) -> TranslationResult:
    ensure_translate_target(target_language)
    ensure_translate_script(target_language, script)
    chunks = split_text(text, max_chunk_chars)
    if not chunks:
        raise BodhanRequestInvalid("There is no text to translate.", code="empty_text")
    result = TranslationResult(target_language, script, "")
    usage = {"prompt": 0, "completion": 0}
    pieces: list[str] = []
    for chunk in chunks:
        segs = await _translate_chunk(client, chunk.text, target_language, script, 0, usage)
        for i, seg in enumerate(segs):
            seg.index = len(result.segments)
            seg.separator = chunk.separator if i == len(segs) - 1 else " "
            result.segments.append(seg)
            pieces.append(seg.translated_text + seg.separator)
    result.translation = "".join(pieces).rstrip()
    result.prompt_tokens, result.completion_tokens = usage["prompt"], usage["completion"]
    return result
