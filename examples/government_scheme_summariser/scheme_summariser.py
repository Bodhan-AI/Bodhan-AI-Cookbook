"""Scheme Saathi: turn a government scheme notice into a scheme card in the reader's language.

Pipeline, Bodhan models only:

    PDF / photo --indic-ocr--> layout blocks --headings--> scheme card (verbatim source lines)
        --indic-translate--> card in the reader's language (+ roman-script copy for WhatsApp)
        --indic-speak--> section-by-section audio
    spoken question --indic-transcribe--> text      (typed roman question --indic-transliterate--> native)
        --indic-translate--> English --extractive match against the card--> source lines --translate + speak--> answer

No general-purpose LLM is involved. Every line on the card and in every answer
is a line from the source document (or its translation), so eligibility rules,
amounts and deadlines are never paraphrased or invented.

CLI:
    python scheme_summariser.py sample_data/sample_scheme_notice.pdf --lang hi ta
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import threading
import time
import warnings
import wave
from collections import deque
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUTPUTS = HERE / "outputs"
NOT_STATED = "Not stated in the document."

# Languages that both indic-translate and indic-speak support, with one voice each.
# Full list: scripts/bodhan_api_rules.json at the repo root.
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "Amit"),
    "hi": ("Hindi", "Kavya"),
    "bn": ("Bengali", "Ishita"),
    "ta": ("Tamil", "Anitha"),
    "te": ("Telugu", "Sravani"),
    "mr": ("Marathi", "Anagha"),
    "gu": ("Gujarati", "Dhara"),
    "kn": ("Kannada", "Deepika"),
    "ml": ("Malayalam", "Lakshmi"),
    "pa": ("Punjabi", "Kaur"),
    "or": ("Odia", "Itishree"),
    "as": ("Assamese", "Prastuti"),
    "ur": ("Urdu", "Saba"),
    "ne": ("Nepali", "Srijana"),
    "mai": ("Maithili", "Vaidehi"),
    "kok": ("Konkani", "Anjali"),
    "doi": ("Dogri", "Preeti"),
    "sa": ("Sanskrit", "Bharati"),
    "brx": ("Bodo", "Gwrbw"),
    "sat": ("Santali", "Phulmani"),
    "mni": ("Manipuri", "Thoibi"),
    "ks": ("Kashmiri", "Zoon"),
}
SPEECH_STYLE = "Customer Care"
DANDA_LANGS ={"hi", "bn", "as", "or", "mai", "ne", "sa", "doi", "brx"}

# Card fields in display order. Keywords are matched against (English) section headings,
# and also used to route questions. Order matters: "not eligible" must win over "eligib".
FIELDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("about", "About the scheme", ("objective", "about", "introduction", "purpose", "overview", "background", "aim")),
    ("exclusions", "Who cannot apply", ("not eligible", "ineligib", "exclusion", "excluded", "who cannot", "who can not")),
    ("eligibility", "Who can apply", ("eligib", "who can apply", "criteria", "qualification", "who can")),
    ("benefits", "What you get", ("benefit", "assistance", "subsidy", "entitlement", "incentive", "amount", "what you get", "financial")),
    ("documents", "Documents needed", ("document", "papers", "enclosure", "attach", "certificate", "proof")),
    ("how_to_apply", "How to apply", ("how to apply", "application process", "procedure", "apply", "registration", "steps", "process")),
    ("deadlines", "Important dates", ("last date", "deadline", "important date", "dates", "timeline", "schedule")),
    ("contact", "Help and contact", ("contact", "helpline", "grievance", "help desk", "toll free", "enquir", "inquir", "support")),
]
FIELD_LABELS = {key: label for key, label, _ in FIELDS}

# Extra words people use in questions (after translation to English) for each field.
QUESTION_HINTS: dict[str, tuple[str, ...]] = {
    "about": ("what is", "scheme about", "purpose", "why"),
    "exclusions": ("not eligible", "cannot", "can't", "excluded", "disqualif"),
    "eligibility": ("eligible", "who can", "can i", "qualify", "age", "income", "land", "farmer", "criteria"),
    "benefits": ("how much", "money", "amount", "benefit", "get", "subsidy", "rupees", "receive", "paid"),
    "documents": ("document", "papers", "proof", "certificate", "aadhaar", "need to bring", "submit", "attach"),
    "how_to_apply": ("how to apply", "how do i apply", "where", "register", "apply", "form", "online", "office"),
    "deadlines": ("last date", "deadline", "when", "date", "until", "till", "by when"),
    "contact": ("contact", "helpline", "phone", "call", "number", "email", "complain", "grievance", "help"),
}

STOPWORDS = set(
    "a an the of to in on for and or is are was were be been with by at as from this that these those it its "
    "i am me my we our you your he she they them their what which who whom how when where why do does did can "
    "will shall should may might must have has had not no yes if then than so such any all each per under "
    "about into over also only other there here".split()
)
DATE_RE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?[\s./-]+(?:\d{1,2}|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)[\s.,/-]+\d{2,4}\b",
    re.IGNORECASE,
)
MONEY_RE = re.compile(r"(?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?(?:\s?(?:lakh|crore))?", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?(?:1800[\s-]?\d{3}[\s-]?\d{4}|\d{3,5}[\s-]\d{6,8}|[6-9]\d{9})(?!\d)")
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s)>\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
SENTENCE_END = re.compile(r"(?<=[.!?।॥])\s+")


# ----------------------------------------------------------------------------
# Bodhan API client
# ----------------------------------------------------------------------------


class BodhanError(RuntimeError):
    pass


class BudgetExceeded(BodhanError):
    pass


def _error_field(resp: requests.Response, name: str) -> str | None:
    try:
        return (resp.json().get("error") or {}).get(name)
    except (ValueError, AttributeError):
        return None


# The console issues each model to one key, so a key per model is common.
# Each variable is optional and falls back to BODHAN_API_KEY.
MODEL_KEY_VARS = {
    "indic-ocr": "BODHAN_OCR_API_KEY",
    "indic-translate": "BODHAN_TRANSLATE_API_KEY",
    "indic-transliterate": "BODHAN_TRANSLITERATE_API_KEY",
    "indic-speak": "BODHAN_SPEAK_API_KEY",
    "indic-transcribe": "BODHAN_TRANSCRIBE_API_KEY",
}


def _usable(value: str | None) -> str | None:
    return value if value and "<" not in value else None


class Bodhan:
    """Thin wrapper over the five Bodhan endpoints used here."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
        keys: dict[str, str] | None = None,
        cache_dir: Path | None = None,
    ):
        """`cache_dir`: keep translations and speech on disk, keyed by their exact input, so a
        re-run (or a live demo) doesn't call or bill the API again. Spoken questions are never cached."""
        self.cache_dir = cache_dir
        default = _usable(api_key) or _usable(os.environ.get("BODHAN_API_KEY"))
        self.keys = {model: _usable((keys or {}).get(model)) or _usable(os.environ.get(var)) or default for model, var in MODEL_KEY_VARS.items()}
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self.session = session or requests.Session()
        self._chat_only: set[str] = set()
        self._lock = threading.Lock()
        self._sent: dict[str, deque] = {}
        self._limits: dict[str, int] = {}

    def _cached(self, kind: str, request: dict, suffix: str, produce):
        if not self.cache_dir:
            return produce()
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        path = self.cache_dir / f"{kind}_{digest}{suffix}"
        if path.exists():
            return path.read_bytes() if suffix == ".wav" else path.read_text(encoding="utf-8")
        value = produce()
        path.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".wav":
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
        return value

    def missing_keys(self) -> list[str]:
        return [model for model, key in self.keys.items() if not key]

    # Keys publish a request budget (x-ratelimit-*-limit-requests, per minute) and a
    # parallel limit. Pace calls client-side so a long card never trips the 429.
    WINDOW_S = 60.0

    def _throttle(self, model: str) -> None:
        with self._lock:
            limit = self._limits.get(model, 60)
            sent = self._sent.setdefault(model, deque())
            now = time.monotonic()
            while sent and now - sent[0] > self.WINDOW_S:
                sent.popleft()
            wait = self.WINDOW_S - (now - sent[0]) + 0.05 if len(sent) >= limit else 0.0
            sent.append(now + wait)
        if wait > 0:
            time.sleep(wait)

    # Two kinds of 429. `rate_limit_exceeded_error` means this key spent its budget: wait out the
    # window and pace lower from then on. "Maximum rate limit reached for the model" is the
    # model's shared capacity (busy for everyone): back off briefly a bounded number of times.
    BUSY_BACKOFF_S = (5.0, 15.0, 30.0, 60.0)

    def _backoff_for_429(self, model: str, resp: requests.Response, attempt: int) -> float:
        code = _error_field(resp, "code")
        retry_after = resp.headers.get("retry-after", "")
        if code == "rate_limit_exceeded_error":
            with self._lock:
                in_window = len(self._sent.get(model, ()))
                self._limits[model] = max(1, min(self._limits.get(model, in_window), in_window) - 2)
            default = self.WINDOW_S
        else:
            default = self.BUSY_BACKOFF_S[attempt]
        try:
            return min(float(retry_after), self.WINDOW_S) if retry_after else default
        except ValueError:
            return default

    def _learn_limit(self, model: str, resp: requests.Response) -> None:
        for header in ("x-ratelimit-api_key-limit-requests", "x-ratelimit-limit-requests"):
            value = resp.headers.get(header)
            if value and value.isdigit():
                learned = max(1, int(int(value) * 0.75))  # margin: the server's window may not align with ours
                self._limits[model] = min(self._limits.get(model, learned), learned)
                return

    def _request(self, method: str, path: str, *, model: str, timeout: int = 120, **kw) -> requests.Response:
        key = self.keys.get(model)
        if not key:
            raise BodhanError(f"no API key for {model}: set {MODEL_KEY_VARS[model]} or BODHAN_API_KEY in .env")
        headers = {"Authorization": f"Bearer {key}", **kw.pop("headers", {})}
        busy_retries, network_retried = 0, False
        while True:
            self._throttle(model)
            try:
                resp = self.session.request(method, f"{self.base_url}{path}", headers=headers, timeout=timeout, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if network_retried:
                    raise BodhanError(f"network error calling {path}: {exc.__class__.__name__}") from exc
                network_retried = True
                time.sleep(3)  # one retry for a dropped connection
                continue
            self._learn_limit(model, resp)
            if resp.status_code == 429 and _error_field(resp, "type") == "budget_exceeded":
                # The key's spending budget is used up: waiting won't help, so stop at once.
                raise BudgetExceeded(f"{model}: the key's budget is used up. Raise it or top up at console.bodhan.ai ({MODEL_KEY_VARS[model]}).")
            if resp.status_code == 429 and busy_retries < len(self.BUSY_BACKOFF_S):
                time.sleep(self._backoff_for_429(model, resp, busy_retries))
                busy_retries += 1
                continue
            break
        if not resp.ok:
            try:
                err = resp.json()["error"]
                message = f"{resp.status_code} {err.get('code')}: {err.get('message')} (request_id={err.get('request_id')})"
            except (ValueError, KeyError, TypeError):
                message = f"{resp.status_code}: {resp.text[:200]}"
            raise BodhanError(message)
        return resp

    def ocr(self, image: bytes, mime: str = "image/png") -> dict:
        b64 = base64.b64encode(image).decode()
        body = {
            "model": "indic-ocr",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
            "table_format": "markdown",
        }
        data = self._request("POST", "/v1/chat/completions", model="indic-ocr", json=body, timeout=180).json()
        return {"markdown": data["choices"][0]["message"]["content"], "blocks": data.get("blocks", [])}

    def _text_call(self, path: str, model: str, result_key: str, text: str, params: dict) -> str:
        """POST /translate or /transliterate; if the gateway doesn't serve that route (404),
        use the equivalent chat-completions transport the API reference documents."""
        if path not in self._chat_only:
            try:
                data = self._request("POST", path, model=model, json={"text": text, **params}, timeout=60).json()
                if data.get("truncated"):
                    warnings.warn(f"{model} output was truncated for: {text[:60]!r}", stacklevel=3)
                return data[result_key]
            except BodhanError as exc:
                if not str(exc).startswith("404"):
                    raise
                self._chat_only.add(path)
        body = {"model": model, "messages": [{"role": "user", "content": text}], **params}
        data = self._request("POST", "/v1/chat/completions", model=model, json=body, timeout=60).json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            warnings.warn(f"{model} output was truncated for: {text[:60]!r}", stacklevel=3)
        return choice["message"]["content"].strip()

    def translate(self, text: str, target_language: str, script: str | None = None) -> str:
        params = {"target_language": target_language, **({"script": script} if script else {})}
        if len(text) > 300:
            # The server sizes the output budget itself and can cut long batches short
            # (roman script especially); give long inputs explicit room.
            params["max_output_tokens"] = min(4096, 2 * len(text))
        request = {"model": "indic-translate", "text": text, **params}
        return self._cached("translate", request, ".txt", lambda: self._text_call("/translate", "indic-translate", "translation", text, params))

    def transliterate(self, text: str, language: str, script: str = "roman") -> str:
        params = {"language": language, "script": script}
        request = {"model": "indic-transliterate", "text": text, **params}
        return self._cached("transliterate", request, ".txt", lambda: self._text_call("/transliterate", "indic-transliterate", "transliteration", text, params))

    def speak(self, text: str, lang: str, voice: str | None = None, style: str | None = SPEECH_STYLE) -> bytes:
        instructions = {"lang": lang, **({"style": style} if style else {})}
        body = {"model": "indic-speak", "input": text, "voice": voice or LANGUAGES[lang][1], "instructions": json.dumps(instructions)}
        return self._cached("speak", body, ".wav", lambda: self._request("POST", "/v1/audio/speech", model="indic-speak", json=body).content)

    def transcribe(self, audio: bytes, language: str | None = None, filename: str = "question.wav") -> str:
        data = {"model": "indic-transcribe", **({"language": language} if language else {})}
        files = {"file": (filename, audio, "audio/wav" if filename.endswith(".wav") else "application/octet-stream")}
        return self._request("POST", "/v1/audio/transcriptions", model="indic-transcribe", files=files, data=data, timeout=60).json()["text"]


# ----------------------------------------------------------------------------
# 1. Document -> page images -> OCR
# ----------------------------------------------------------------------------


def document_to_pages(data: bytes, filename: str, dpi: int = 150, max_pages: int = 10, max_side: int = 2200) -> list[bytes]:
    """Rasterise a PDF (or normalise a photo) into PNG pages for indic-ocr, which takes one image per request."""
    import pymupdf

    suffix = Path(filename).suffix.lower().lstrip(".") or "pdf"
    if suffix == "pdf":
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return [page.get_pixmap(dpi=dpi).tobytes("png") for page in list(doc)[:max_pages]]
    pix = pymupdf.Pixmap(data)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.n not in (1, 3):  # CMYK scans
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    while max(pix.width, pix.height) > max_side:
        pix.shrink(1)  # halve each side
    return [pix.tobytes("png")]


def ocr_pages(client: Bodhan, pages: list[bytes], cache_dir: Path | None = OUTPUTS / "cache") -> list[dict]:
    """OCR each page. Results are cached by image hash, because OCR is billed per page."""

    def one(png: bytes) -> dict:
        path = cache_dir / f"ocr_{hashlib.sha256(png).hexdigest()[:16]}.json" if cache_dir else None
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        result = client.ocr(png)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(one, pages))


# ----------------------------------------------------------------------------
# 2. OCR Markdown -> scheme card (deterministic, verbatim)
# ----------------------------------------------------------------------------


@dataclass
class Section:
    heading: str  # heading as printed in the document
    lines: list[str] = field(default_factory=list)


@dataclass
class SchemeCard:
    title: str
    fields: dict[str, list[str]]  # field key -> verbatim source lines ([] when not stated)
    headings: dict[str, str]  # field key -> heading the lines came from
    facts: dict[str, list[str]]  # dates / amounts / phones / links found anywhere
    source_lang: str = "en"

    def to_markdown(self, labels: dict[str, str] | None = None, not_stated: str = NOT_STATED, title: str | None = None) -> str:
        labels = labels or FIELD_LABELS
        out = [f"# {title or self.title}", ""]
        for key, _, _ in FIELDS:
            out.append(f"## {labels[key]}")
            lines = self.fields.get(key) or []
            out += [f"- {line}" for line in lines] if lines else [f"_{not_stated}_"]
            out.append("")
        return "\n".join(out).strip() + "\n"


def _clean(line: str) -> str:
    line = re.sub(r"<[^>]+>", " ", line)  # stray HTML from tables
    line = re.sub(r"^\s*(?:[-*•▪●]|\(?[0-9ivx]{1,4}[.)])\s+", "", line, flags=re.IGNORECASE)  # bullets / numbering
    line = line.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", line).strip(" |:-\t")


def _table_row(line: str) -> str | None:
    """Turn a Markdown table row into 'a — b — c'; drop separator rows."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
        return None
    return " — ".join(_clean(c) for c in cells if _clean(c))


def _heading_text(line: str) -> str | None:
    s = line.strip()
    if m := re.match(r"^#{1,6}\s+(.*)$", s):
        return _clean(m.group(1))
    if m := re.fullmatch(r"\*\*(.{2,80}?)\*\*:?", s):
        return _clean(m.group(1))
    plain = _clean(s)
    words = plain.split()
    if s.endswith(":") and 0 < len(words) <= 8:
        return plain
    if 1 <= len(words) <= 8 and plain.isupper() and any(c.isalpha() for c in plain):
        return plain
    # A short unpunctuated line that names a card section ("Eligibility", "How to apply").
    if 1 <= len(words) <= 6 and not re.search(r"[.!?।,;]$", plain) and plain[:1].isupper() and classify_heading(plain):
        return plain
    return None


HEADING_BLOCK_TYPES = {"SectionHeader", "Title"}
SKIP_BLOCK_TYPES = {"PageHeader", "PageFooter"}


def ocr_to_markdown(results: list[dict]) -> str:
    """Rebuild Markdown from indic-ocr layout blocks, marking SectionHeader/Title blocks as headings.

    The Markdown `content` often prints headings as plain lines; the blocks say which lines are
    headings. Text repeated on several pages (watermarks, running headers) is kept once.
    """
    out, seen = [], set()
    for result in results:
        blocks = sorted(result.get("blocks") or [], key=lambda b: b.get("order", 0))
        if not blocks:
            out.append(result.get("markdown", ""))
            continue
        for block in blocks:
            text = (block.get("text") or "").strip()
            if not text or block.get("type") in SKIP_BLOCK_TYPES or text in seen:
                continue
            seen.add(text)
            if block.get("type") in HEADING_BLOCK_TYPES and "\n" not in text:
                out.append(f"## {text}")
            elif block.get("type") == "Footnote":
                out.append(f"## Note\n{_unwrap(text)}")  # signatures and footnotes: kept out of the card sections
            elif block.get("type") == "Table":
                out.append(text)
            else:
                out.append(_unwrap(text))
    return "\n\n".join(out)


LIST_ITEM = re.compile(r"^\s*(?:[-*•▪●]|\(?[0-9ivx]{1,4}[.)])\s+", re.IGNORECASE)


def _unwrap(text: str) -> str:
    """Join lines the page wrapped mid-sentence; keep list items on their own lines."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if lines and not LIST_ITEM.match(line) and not re.search(r"[.!?।:]$", lines[-1]):
            lines[-1] = lines[-1] + line if lines[-1].endswith("-") else f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(lines)


def split_sections(markdown: str) -> list[Section]:
    sections = [Section(heading="")]
    for raw in markdown.splitlines():
        if not raw.strip():
            continue
        if raw.strip().startswith("|"):
            row = _table_row(raw)
            if row:
                sections[-1].lines.append(row)
            continue
        heading = _heading_text(raw)
        if heading:
            sections.append(Section(heading=heading))
            continue
        line = _clean(raw)
        if line:
            sections[-1].lines.append(line)
    return [s for s in sections if s.heading or s.lines]


def classify_heading(heading: str) -> str | None:
    h = heading.lower()
    for key, _, keywords in FIELDS:
        if any(k in h for k in keywords):
            return key
    return None


def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return not letters or sum(c.isascii() for c in letters) / len(letters) > 0.8


def find_facts(text: str) -> dict[str, list[str]]:
    def uniq(items: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(i.strip().rstrip(".,;") for i in items))

    return {
        "dates": uniq(DATE_RE.findall(text)),
        "amounts": uniq(MONEY_RE.findall(text)),
        "phones": uniq(PHONE_RE.findall(text)),
        "links": uniq(URL_RE.findall(text) + EMAIL_RE.findall(text)),
    }


SCHEME_NAME_RE = re.compile(r"yojana|yojna|scheme|mission|abhiyan|programme|program|scholarship|pension|nidhi|bima|योजना|மிஷன்|திட்ட|প্রকল্প|যোজনা", re.I)


def pick_title(sections: list[Section]) -> str:
    """The scheme's name: a line before the first field section that looks like a scheme name,
    else the first heading that isn't a card field, else the first line."""
    leading: list[str] = []
    for section in sections:
        if section.heading and classify_heading(section.heading):
            break
        leading += ([section.heading] if section.heading else []) + section.lines
    named = [line for line in leading if SCHEME_NAME_RE.search(line) and len(line.split()) <= 14]
    if named:
        return min(named, key=len)
    headings = [s.heading for s in sections if s.heading and not classify_heading(s.heading)]
    return (headings or leading or ["Scheme notice"])[0]


def build_card(markdown: str, client: Bodhan | None = None) -> SchemeCard:
    """Map document sections onto card fields by their headings. No text is rewritten.

    Headings in a non-Latin script are translated to English first (headings only, a few
    short calls) so the same keyword rules work for Hindi, Tamil or Bengali notices.
    """
    sections = split_sections(markdown)
    source_lang = "en" if _is_latin(markdown) else "auto"
    title = pick_title(sections)

    fields: dict[str, list[str]] = {key: [] for key, _, _ in FIELDS}
    headings: dict[str, str] = {}
    for section in sections:
        heading_en = section.heading
        if heading_en and client and not _is_latin(heading_en):
            heading_en = client.translate(heading_en, "en")
        key = classify_heading(heading_en) if heading_en else None
        if key and section.lines:
            fields[key] += section.lines
            headings.setdefault(key, section.heading)

    facts = find_facts(markdown)
    # Fallbacks for notices without clear headings: pull lines that carry the fact itself.
    all_lines = [line for s in sections for line in s.lines]
    if not fields["deadlines"]:
        fields["deadlines"] = [ln for ln in all_lines if DATE_RE.search(ln) and re.search(r"last|deadline|before|by|till|until", ln, re.I)]
    if not fields["contact"]:
        fields["contact"] = [ln for ln in all_lines if PHONE_RE.search(ln) or EMAIL_RE.search(ln) or URL_RE.search(ln)]
    if not fields["about"]:
        first = next((s for s in sections if s.lines and classify_heading(s.heading or "") is None), None)
        fields["about"] = first.lines[:2] if first else []
    return SchemeCard(title=title, fields=fields, headings=headings, facts=facts, source_lang=source_lang)


# ----------------------------------------------------------------------------
# 3. Card -> reader's language (+ roman script copy)
# ----------------------------------------------------------------------------


@dataclass
class LocalCard:
    lang: str
    title: str
    labels: dict[str, str]
    not_stated: str
    fields: dict[str, list[str]]
    roman: LocalCard | None = None  # the same card in roman script, for languages other than English

    def to_markdown(self) -> str:
        out = [f"# {self.title}", ""]
        for key, _, _ in FIELDS:
            out.append(f"## {self.labels[key]}")
            out += [f"- {line}" for line in self.fields.get(key, [])] or [f"_{self.not_stated}_"]
            out.append("")
        return "\n".join(out).strip() + "\n"

    def to_whatsapp(self, roman: bool = True) -> str:
        """Plain text with WhatsApp bold markers. Roman script by default: easy to forward and type a reply to."""
        card = self.roman if roman and self.roman else self
        out = [f"*{card.title}*", ""]
        for key, _, _ in FIELDS:
            if card.fields.get(key):
                out.append(f"*{card.labels[key]}*")
                out += [f"• {line}" for line in card.fields[key]]
                out.append("")
        return "\n".join(out).strip()


def localise_card(client: Bodhan, card: SchemeCard, lang: str, roman: bool = True, workers: int = 2) -> LocalCard:
    if lang == "en" and card.source_lang == "en":
        return LocalCard("en", card.title, dict(FIELD_LABELS), NOT_STATED, {k: list(v) for k, v in card.fields.items()})

    keys = [key for key, _, _ in FIELDS]
    jobs: list[str] = [card.title, NOT_STATED] + [FIELD_LABELS[k] for k in keys]
    spans: dict[str, tuple[int, int]] = {}
    for key in keys:
        start = len(jobs)
        jobs += card.fields.get(key, [])
        spans[key] = (start, len(jobs))
    # One request per section, not per line: ~10 calls per language instead of ~40.
    batches = [(0, 2 + len(keys))] + [span for span in spans.values() if span[1] > span[0]]

    def translate_all(script: str | None) -> list[str]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = pool.map(lambda span: translate_lines(client, jobs[span[0] : span[1]], lang, script), batches)
        return [line for part in parts for line in part]

    translated = translate_all(None)

    def assemble(texts: list[str]) -> LocalCard:
        return LocalCard(
            lang=lang,
            title=texts[0],
            not_stated=texts[1],
            labels=dict(zip(keys, texts[2 : 2 + len(keys)], strict=True)),
            fields={key: texts[a:b] for key, (a, b) in spans.items()},
        )

    local = assemble(translated)
    if roman and lang != "en":
        # Roman script straight from the source in one step (translate's script="roman").
        local.roman = assemble(translate_all("roman"))
    return local


def translate_lines(client: Bodhan, lines: list[str], lang: str, script: str | None = None) -> list[str]:
    """Translate several lines in one request, keeping one output line per input line.
    If the model merges or splits lines, retry each half separately, so alignment never drifts
    and a single odd line costs a few extra requests rather than one per line."""
    if len(lines) == 1:
        return [client.translate(lines[0], lang, script=script)]
    out = [p.strip() for p in client.translate("\n".join(lines), lang, script=script).split("\n") if p.strip()]
    if len(out) == len(lines):
        return out
    mid = len(lines) // 2
    return translate_lines(client, lines[:mid], lang, script) + translate_lines(client, lines[mid:], lang, script)


# ----------------------------------------------------------------------------
# 4. Speech: chunk, synthesise, join
# ----------------------------------------------------------------------------


def speech_chunks(text: str, max_chars: int = 220) -> list[str]:
    """indic-speak takes a sentence or two per request (~30 s). Split on sentence ends (then commas
    for long sentences) and pack short sentences together, so a card needs fewer requests."""
    pieces: list[str] = []
    for sentence in (s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()):
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(sep, 0, max_chars) for sep in (",", ";", "—", " "))
            cut = cut if cut > max_chars // 3 else max_chars
            pieces.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            pieces.append(sentence)
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_chars:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def join_wavs(clips: Iterable[bytes], gap_ms: int = 250) -> bytes:
    frames, params = [], None
    for clip in clips:
        with contextlib.closing(wave.open(io.BytesIO(clip), "rb")) as wf:
            params = params or wf.getparams()
            frames.append(wf.readframes(wf.getnframes()))
    if params is None:
        return b""
    silence = b"\x00" * int(params.framerate * gap_ms / 1000) * params.sampwidth * params.nchannels
    buf = io.BytesIO()
    with contextlib.closing(wave.open(buf, "wb")) as out:
        out.setparams(params)
        out.writeframes(silence.join(frames))
    return buf.getvalue()


def speak_text(client: Bodhan, text: str, lang: str, workers: int = 2) -> bytes:
    chunks = speech_chunks(text)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return join_wavs(pool.map(lambda c: client.speak(c, lang), chunks))


def speak_card(client: Bodhan, card: LocalCard, workers: int = 2) -> dict[str, bytes]:
    """One clip per card section, so a listener can jump straight to 'Documents needed'."""
    stop = "।" if card.lang in DANDA_LANGS else "."
    clips: dict[str, bytes] = {}
    for key, _, _ in FIELDS:
        lines = card.fields.get(key) or [card.not_stated]
        script = f"{card.labels[key]}{stop} " + " ".join(line.rstrip("।. ") + stop for line in lines)
        clips[key] = speak_text(client, script, card.lang, workers)
    return clips


# ----------------------------------------------------------------------------
# 5. Questions: transcribe, translate, match, answer
# ----------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9₹]+", text.lower())
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())}


@dataclass
class Answer:
    field: str | None  # card field the question was routed to, if any
    source_lines: list[str]  # verbatim lines from the card (source language)
    question_en: str = ""

    @property
    def found(self) -> bool:
        return bool(self.source_lines)


def route_question(question_en: str) -> str | None:
    q = question_en.lower()
    scores = {key: sum(len(h) for h in hints if re.search(rf"\b{re.escape(h)}", q)) for key, hints in QUESTION_HINTS.items()}
    key, score = max(scores.items(), key=lambda kv: kv[1])
    return key if score else None


def answer_question(card: SchemeCard, question_en: str, max_lines: int = 8) -> Answer:
    """Extractive answer: lines from the card, never a generated sentence.

    1. A line anywhere that shares more words with the question than anything in the routed
       section wins ("government employee, can I apply?" -> the exclusion line).
    2. Otherwise use the routed section ("which documents" -> documents): only its lines that
       match well ("tenant farmer" -> the tenant line), or the whole section if none stand out.
    """
    routed = route_question(question_en)
    q = _tokens(question_en)
    line_tokens = [(key, line, _tokens(line)) for key, lines in card.fields.items() for line in lines]
    # Rarer words carry more weight when two lines share the same number of words with the question.
    df: dict[str, int] = {}
    for _, _, toks in line_tokens:
        for tok in toks:
            df[tok] = df.get(tok, 0) + 1
    n = max(len(line_tokens), 1)
    scored = [(len(q & toks), sum(math.log(1 + n / df[t]) for t in q & toks), key, line) for key, line, toks in line_tokens]
    best_anywhere = max((s for s, *_ in scored), default=0)
    in_routed = [(s, line) for s, _, key, line in scored if key == routed]
    best_routed = max((s for s, _ in in_routed), default=-1)

    if best_anywhere >= 2 and best_anywhere > best_routed:
        top = max(scored, key=lambda t: (t[0], t[1]))
        top_key = top[2]
        strong = [t for t in scored if (t[2] == top_key and t[0] >= max(2, best_anywhere - 1)) or (t[0] == best_anywhere and t[1] >= top[1] - 1e-6)]
        strong.sort(key=lambda t: (-t[0], -t[1]))
        return Answer(field=top_key, source_lines=[t[3] for t in strong][:max_lines], question_en=question_en)
    if not in_routed:
        return Answer(field=routed, source_lines=[], question_en=question_en)
    picked = [line for s, line in in_routed if s >= max(2, best_routed - 1)] if best_routed >= 2 else [line for _, line in in_routed]
    return Answer(field=routed, source_lines=picked[:max_lines], question_en=question_en)


@dataclass
class SpokenAnswer:
    question: str  # as heard / typed, in the reader's language
    question_en: str
    answer: Answer
    text: str  # answer in the reader's language
    audio: bytes = b""


def english_card(client: Bodhan, card: SchemeCard) -> SchemeCard:
    """Questions are matched in English. For a notice in another script, translate its lines once."""
    if card.source_lang == "en":
        return card
    local = localise_card(client, card, "en", roman=False)
    return SchemeCard(title=local.title, fields=local.fields, headings=card.headings, facts=card.facts, source_lang="en")


def ask(client: Bodhan, card: SchemeCard, lang: str, *, audio: bytes | None = None, text: str | None = None, speak: bool = True) -> SpokenAnswer:
    """Answer a question about the notice. `card` must be English: pass `english_card(client, card)`."""
    if audio is not None:
        question = client.transcribe(audio, lang)
    elif text:
        question = text
        if lang not in ("en",) and text.isascii():
            # Typed on an English keyboard ("kaun se documents chahiye"): convert to native script first.
            question = client.transliterate(text, lang, "native")
    else:
        raise ValueError("pass audio= or text=")

    question_en = question if lang == "en" else client.translate(question, "en")
    answer = answer_question(card, question_en)
    if answer.found:
        label = FIELD_LABELS.get(answer.field or "", "From the notice")
        reply_en = f"{label}: " + " ".join(line.rstrip(".") + "." for line in answer.source_lines)
    else:
        reply_en = "The notice does not answer this question. Please contact the helpline or the scheme office."
    reply = reply_en if lang == "en" else client.translate(reply_en, lang)
    return SpokenAnswer(question, question_en, answer, reply, speak_text(client, reply, lang) if speak else b"")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def run(path: Path, langs: list[str], out_dir: Path = OUTPUTS, audio: bool = True) -> SchemeCard:
    client = Bodhan(cache_dir=OUTPUTS / "cache")
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = document_to_pages(path.read_bytes(), path.name)
    print(f"{path.name}: {len(pages)} page(s)")
    ocr = ocr_pages(client, pages)
    markdown = ocr_to_markdown(ocr)
    (out_dir / "ocr.md").write_text(markdown, encoding="utf-8")
    card = build_card(markdown, client)
    (out_dir / "card_source.md").write_text(card.to_markdown(), encoding="utf-8")
    (out_dir / "card_source.json").write_text(json.dumps(asdict(card), ensure_ascii=False, indent=2), encoding="utf-8")
    missing = [FIELD_LABELS[k] for k, v in card.fields.items() if not v]
    print(f"card: {card.title!r}; not stated: {', '.join(missing) or 'none'}")

    for lang in langs:
        local = localise_card(client, card, lang)
        (out_dir / f"card_{lang}.md").write_text(local.to_markdown(), encoding="utf-8")
        (out_dir / f"card_{lang}_whatsapp.txt").write_text(local.to_whatsapp(), encoding="utf-8")
        if audio:
            clips = speak_card(client, local)
            (out_dir / f"card_{lang}.wav").write_bytes(join_wavs(clips.values(), gap_ms=600))
        print(f"{LANGUAGES[lang][0]:10} -> {out_dir / f'card_{lang}.md'}" + (" + .wav" if audio else ""))
    return card


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
    parser = argparse.ArgumentParser(description="Turn a scheme notice into a scheme card in Indian languages.")
    parser.add_argument("document", type=Path, help="PDF, PNG or JPEG")
    parser.add_argument("--lang", nargs="+", default=["hi"], choices=sorted(LANGUAGES), help="card languages")
    parser.add_argument("--no-audio", action="store_true", help="skip indic-speak")
    parser.add_argument("--out", type=Path, default=OUTPUTS)
    args = parser.parse_args(argv)
    run(args.document, args.lang, args.out, audio=not args.no_audio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
