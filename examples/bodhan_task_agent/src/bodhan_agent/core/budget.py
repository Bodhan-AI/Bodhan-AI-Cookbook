from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bodhan_agent.schemas.budget import MinimumCost, Quote, Reservation
from bodhan_agent.schemas.ledger import Artifact
from bodhan_mcp.config import Settings
from bodhan_mcp.schemas.ocr import parse_pages
from bodhan_mcp.tools.synthesize import SPEAK_CHUNK_CHARS, plain_text
from bodhan_mcp.utils.pricing import ocr_cost, speak_cost, transcribe_cost
from bodhan_mcp.utils.text import split_for_speech, split_text

TRANSLATE_MAX_TOKENS = 4096
TRANSLATE_INR_PER_TOKEN = 0.20 / 10_000
VERIFY_CHARS_PER_SECOND = 5.0
FREE_TOOLS: frozenset[str] = frozenset({"read", "write", "detect_language", "finish"})
DOCUMENT_KINDS: frozenset[str] = frozenset({"pdf", "image"})
MEDIA_KINDS: frozenset[str] = frozenset({"audio", "video"})
_EPSILON = 1e-9


def _limits() -> Settings:
    return Settings.from_env()


def _round(value: float) -> float:
    return round(value, 6)


def quote_free(tool: str) -> Quote:
    return Quote(tool=tool, amount_inr=0.0, bound="exact")


def quote_ocr(pages: int | None, tool: str = "ocr_document") -> Quote:
    cap = _limits().ocr_max_pages
    if pages is None:
        amount = ocr_cost(cap)
        return Quote(tool=tool, amount_inr=amount, bound="assumed", parts={"ocr": amount},
                     notes=[f"page count unknown; reserved the {cap}-page limit"])  # fmt: skip
    amount = ocr_cost(min(pages, cap))
    return Quote(tool=tool, amount_inr=amount, bound="exact", parts={"ocr": amount})


def quote_transcribe(duration_s: float | None, tool: str = "transcribe_audio") -> Quote:
    if duration_s is None or not math.isfinite(duration_s):
        cap = _limits().transcribe_max_duration_s
        amount = transcribe_cost(cap)
        return Quote(tool=tool, amount_inr=amount, bound="assumed", parts={"transcribe": amount},
                     notes=[f"audio length unknown; reserved the {cap:.0f} s limit"])  # fmt: skip
    amount = transcribe_cost(duration_s)
    return Quote(tool=tool, amount_inr=amount, bound="upper", parts={"transcribe": amount})


def translate_requests(text: str) -> int:
    return len(split_text(text, _limits().translate_chunk_chars)) if text.strip() else 0


def quote_translate(text: str, tool: str = "translate_text", extra_requests: int = 0) -> Quote:
    requests = translate_requests(text) + extra_requests
    amount = _round(requests * TRANSLATE_MAX_TOKENS * TRANSLATE_INR_PER_TOKEN)
    return Quote(
        tool=tool,
        amount_inr=amount,
        bound="assumed",
        parts={"translate": amount},
        notes=[f"{requests} request(s) at up to {TRANSLATE_MAX_TOKENS} output tokens each"],
    )


def quote_speech(text: str, verify: bool = True, tool: str = "synthesize_speech") -> Quote:
    chunks = split_for_speech(plain_text(text), SPEAK_CHUNK_CHARS)
    chars = sum(len(chunk) for chunk in chunks)
    parts = {"speak": speak_cost(chars)}
    notes = [f"{len(chunks)} speech request(s), {chars} characters"]
    if verify:
        parts["transcribe"] = transcribe_cost(chars / VERIFY_CHARS_PER_SECOND)
        notes.append("includes back-transcription of the generated audio")
    return Quote(tool=tool, amount_inr=_round(sum(parts.values())), bound="upper", parts=parts, notes=notes)


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot price this call: {path} could not be read as text") from exc


def _segment_texts(path: str) -> list[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot price this call: {path} is not a readable segments file") from exc
    segments = data.get("segments", data) if isinstance(data, dict) else data
    if not isinstance(segments, list):
        raise ValueError(f"cannot price this call: {path} has no segment list")
    return [str(item.get("text", "")) for item in segments if isinstance(item, dict) and item.get("text")]


def _source_text(args: dict[str, Any]) -> str:
    if isinstance(args.get("text"), str):
        return args["text"]
    if isinstance(args.get("text_path"), str):
        return _read_text(args["text_path"])
    raise ValueError("cannot price this call: give text or text_path")


def quote_tool(tool: str, args: dict[str, Any], source: Artifact | None = None) -> Quote:
    if tool in FREE_TOOLS:
        return quote_free(tool)
    if tool == "ocr_document":
        if isinstance(args.get("pages"), str) and args["pages"].strip():
            return quote_ocr(len(parse_pages(args["pages"])))
        if source is not None and source.kind == "image":
            return quote_ocr(1)
        return quote_ocr(source.pages if source is not None else None)
    if tool == "transcribe_audio":
        return quote_transcribe(source.duration_s if source is not None else None)
    if tool == "translate_text":
        return quote_translate(_source_text(args))
    if tool == "synthesize_speech":
        return quote_speech(_source_text(args), verify=args.get("verify", True) is not False)
    if tool == "make_subtitles":
        texts = _segment_texts(str(args.get("segments_path", "")))
        return quote_translate("\n".join(texts), tool=tool, extra_requests=len(texts))
    raise ValueError(f"cannot price unknown tool '{tool}'")


def minimum_cost(inputs: Iterable[Artifact], tools: Iterable[str]) -> MinimumCost:
    planned = set(tools)
    items: dict[str, float] = {}
    unknown: list[str] = []
    for artifact in inputs:
        if not artifact.usable:
            continue
        if artifact.kind in DOCUMENT_KINDS and "ocr_document" in planned:
            pages = 1 if artifact.kind == "image" else artifact.pages
            if pages is None:
                unknown.append(f"OCR of {artifact.id} (page count unknown)")
            else:
                items[f"{artifact.id} ocr"] = quote_ocr(pages).amount_inr
        if artifact.kind in MEDIA_KINDS and "transcribe_audio" in planned:
            if artifact.duration_s is None:
                unknown.append(f"transcription of {artifact.id} (length unknown)")
            else:
                items[f"{artifact.id} transcribe"] = quote_transcribe(artifact.duration_s).amount_inr
    for tool, label in (
        ("translate_text", "translation (length depends on earlier steps)"),
        ("synthesize_speech", "speech synthesis (length depends on earlier steps)"),
        ("make_subtitles", "subtitle translation (length depends on the transcript)"),
    ):
        if tool in planned:
            unknown.append(label)
    return MinimumCost(known_inr=_round(sum(items.values())), items=items, unknown=unknown)


class BudgetManager:
    def __init__(self, limit_inr: float) -> None:
        if not math.isfinite(limit_inr) or limit_inr < 0:
            raise ValueError("limit_inr must be a finite number >= 0")
        self.limit_inr = limit_inr
        self.billing_uncertain = False
        self.possibly_billed_failed_attempts = 0
        self.cache_hits = 0
        self._spent = 0.0
        self._open: dict[str, Reservation] = {}
        self._lock = asyncio.Lock()
        self._counter = 0

    @property
    def spent_inr(self) -> float:
        return _round(self._spent)

    @property
    def reserved_inr(self) -> float:
        return _round(sum(item.amount_inr for item in self._open.values()))

    @property
    def remaining_inr(self) -> float:
        return _round(max(0.0, self.limit_inr - self._spent - self.reserved_inr))

    @property
    def open_reservations(self) -> list[Reservation]:
        return list(self._open.values())

    def fits(self, amount_inr: float) -> bool:
        return amount_inr <= self.remaining_inr + _EPSILON

    async def reserve(self, quote: Quote) -> Reservation | None:
        async with self._lock:
            if not self.fits(quote.amount_inr):
                return None
            self._counter += 1
            reservation = Reservation(id=f"R{self._counter}", tool=quote.tool, amount_inr=quote.amount_inr)
            self._open[reservation.id] = reservation
            return reservation

    async def settle(
        self,
        reservation: Reservation,
        charged_inr: float | None,
        *,
        uncertain: bool = False,
        possibly_billed_failed_attempts: int = 0,
        cache_hits: int = 0,
    ) -> float:
        if charged_inr is not None and (not math.isfinite(charged_inr) or charged_inr < 0):
            raise ValueError("charged_inr must be a finite number >= 0")
        async with self._lock:
            if self._open.pop(reservation.id, None) is None:
                raise ValueError(f"reservation {reservation.id} is not open")
            known = charged_inr or 0.0
            charge = max(known, reservation.amount_inr) if uncertain or charged_inr is None else known
            self._spent += charge
            self.billing_uncertain = self.billing_uncertain or uncertain or charged_inr is None
            self.possibly_billed_failed_attempts += possibly_billed_failed_attempts
            self.cache_hits += cache_hits
            return _round(charge)

    async def release(self, reservation: Reservation) -> None:
        async with self._lock:
            if self._open.pop(reservation.id, None) is None:
                raise ValueError(f"reservation {reservation.id} is not open")

    def cost_source(self) -> float:
        return self.spent_inr
