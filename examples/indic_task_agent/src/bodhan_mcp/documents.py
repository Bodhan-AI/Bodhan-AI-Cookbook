"""Images and PDFs -> OCR-ready page images; text files -> text.

PDF text layers are read with pypdfium2 (a PDF library, not OCR). Indic PDFs often
carry broken text layers from legacy fonts, so a text layer is used only when it
looks clean; otherwise the page is rendered and sent to Indic-OCR.
"""

from __future__ import annotations

import functools
import io
import threading
from dataclasses import dataclass
from pathlib import Path

from . import errors as E
from .languages import script_profile
from .media import AUDIO_EXT, VIDEO_EXT

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".rst", ".text"}
PDF_EXT = {".pdf"}
MAX_SIDE = 2400  # px; large phone photos are downscaled before upload
MAX_PDF_PAGES = 60

# PDFium is not thread-safe: concurrent page renders crashed natively in testing.
# Every PDFium call in this process goes through one lock.
_PDFIUM_LOCK = threading.Lock()


def _pdfium_serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _PDFIUM_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT and ext != ".webm":
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in TEXT_EXT:
        return "text"
    head = _head(path, 16)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff":
        return "image"
    if head[:4] == b"RIFF" or head[:3] == b"ID3":
        return "audio"
    return "text" if _looks_utf8(_head(path, 65536)) else "unknown"


def _head(path: Path, n: int) -> bytes:
    """Read at most n bytes: classifying a file must never load a large file whole."""
    try:
        with path.open("rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _looks_utf8(sample: bytes) -> bool:
    for cut in range(4):  # the sample may end in the middle of a multi-byte character
        try:
            (sample[: len(sample) - cut] if cut else sample).decode("utf-8")
            return bool(sample)
        except UnicodeDecodeError:
            continue
    return False


def read_text_file(path: Path, max_bytes: int = 5_000_000) -> str:
    if path.stat().st_size > max_bytes:
        raise E.ToolError(E.INVALID_ARGUMENT, f"{path.name} is larger than {max_bytes // 1_000_000} MB.", hint="Split the file.")
    raw = path.read_bytes()
    return decode_text(raw, path.name)


def _looks_utf16(raw: bytes) -> bool:
    """UTF-16 without a BOM: ASCII-range text leaves a zero byte in every other position."""
    sample = raw[:4000]
    if len(sample) < 4:
        return False
    zeros_even, zeros_odd = sample[0::2].count(0), sample[1::2].count(0)
    half = len(sample) / 2
    return max(zeros_even, zeros_odd) > 0.3 * half and min(zeros_even, zeros_odd) < 0.05 * half


def decode_text(raw: bytes, name: str = "the file") -> str:
    """UTF-8 (with or without BOM); UTF-16 only when it really is UTF-16 (a BOM, or the zero-byte pattern);
    otherwise Windows-1252, the usual legacy encoding of Windows text files. Decoding arbitrary bytes as UTF-16
    would turn a Windows-1252 file into CJK characters."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or _looks_utf16(raw):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    good = raw.decode("utf-8", errors="replace")
    valid = sum(ord(ch) > 127 and ch != "\ufffd" for ch in good)  # characters from valid multi-byte UTF-8
    if valid and valid >= good.count("\ufffd"):
        # mostly UTF-8 with a few bad bytes: keep the UTF-8 text, the bad bytes show as U+FFFD (cp1252 would garble it all)
        return good
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"{name} is not UTF-8, UTF-16 or Windows-1252 text.", hint="Save the file as UTF-8.") from None


@dataclass
class PageImage:
    page: int  # 1-based
    data: bytes
    mime: str
    width: int
    height: int


def _encode_image(img, prefer_png: bool) -> tuple[bytes, str]:
    from PIL import Image

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")  # transparent areas would turn black (often under black text): put them on white paper
        img = Image.new("RGB", rgba.size, (255, 255, 255))
        img.paste(rgba, mask=rgba.split()[-1])
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    if prefer_png:
        img.save(buf, "PNG", optimize=True)
        if buf.tell() <= 4_000_000:
            return buf.getvalue(), "image/png"
        buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def image_frame_count(path: Path) -> int:
    from PIL import Image

    try:
        with Image.open(path) as im:
            return max(1, getattr(im, "n_frames", 1)) if path.suffix.lower() in (".tif", ".tiff") or im.format == "TIFF" else 1
    except Exception as exc:
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"Could not open image {path.name}: {exc}",
                          hint="Supported: PNG, JPEG, WebP, BMP, TIFF (HEIC only if Pillow can read it).") from exc


def load_image(path: Path, frame: int = 1) -> PageImage:
    """Load one page of an image. Multi-page TIFFs are documents: each frame is a page
    (other animated formats such as GIF use their first frame only)."""
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            if frame > 1:
                im.seek(frame - 1)
            im = ImageOps.exif_transpose(im)  # phone photos carry rotation in EXIF
            im.load()
            prefer_png = path.suffix.lower() == ".png"
            data, mime = _encode_image(im, prefer_png)
            size = im.size
    except E.ToolError:
        raise
    except Exception as exc:
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"Could not open image {path.name}: {exc}",
                          hint="Supported: PNG, JPEG, WebP, BMP, TIFF (HEIC only if Pillow can read it).") from exc
    w, h = size
    s = min(1.0, MAX_SIDE / max(w, h))
    return PageImage(frame, data, mime, int(w * s), int(h * s))


@_pdfium_serialized
def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise E.ToolError(E.UNSUPPORTED_FILE_TYPE, f"Could not open PDF {path.name}: {exc}", hint="The PDF may be encrypted or corrupt.") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


@_pdfium_serialized
def pdf_text_layer(path: Path, page_index: int) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_index]
        tp = page.get_textpage()
        text = tp.get_text_range() or ""
        tp.close()
        page.close()
        return text
    finally:
        pdf.close()


@_pdfium_serialized
def pdf_raster_share(path: Path, page_index: int) -> float:
    """Share of the page area covered by embedded images (0..1). A page whose body is a
    scanned picture can still carry a clean digital header; its text layer alone would
    silently drop the scanned part, so such pages are OCRed."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_index]
        w, h = page.get_size()
        area = 0.0
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
            left, bottom, right, top = obj.get_bounds()
            cw = max(0.0, min(right, w) - max(left, 0.0))
            ch = max(0.0, min(top, h) - max(bottom, 0.0))
            area += cw * ch
        page.close()
        return min(1.0, area / max(w * h, 1.0))
    finally:
        pdf.close()


RASTER_OCR_THRESHOLD = 0.15  # images covering more of the page than this (not just a logo) force OCR


def text_layer_is_usable(text: str) -> bool:
    """A text layer is trusted only if it has real content and looks like clean
    script (few replacement/private-use characters, which legacy Indic fonts produce)."""
    stripped = "".join(text.split())
    if len(stripped) < 40:
        return False
    bad = sum(1 for ch in stripped if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF or ord(ch) < 32)
    if bad / len(stripped) > 0.02:
        return False
    prof = script_profile(text)
    letters = sum(prof.values())
    if letters / len(stripped) <= 0.5:
        return False
    if prof.get("Latin", 0) > 0.8 * letters and looks_like_legacy_font(text):
        # Legacy Hindi fonts (Kruti Dev, Chanakya...) store Devanagari as Latin letters, e.g. "fo|ky; ds Nk=ksa":
        # such a layer is not trusted, so the page is read by OCR instead.
        return False
    return True


_LEGACY_WORDS = frozenset("ds dk dh esa gS gSa vkSj fd us ;g og Fkk Fkh rFkk ,d dks ls".split())


def looks_like_legacy_font(text: str) -> bool:
    """Positive evidence of a legacy Devanagari font: symbols inside words (fo|ky;, Nk=ksa, 'kq:vkr) or the
    letter patterns that its most common words become (ds, dk, esa, gS, vkSj...). Plain English never has these."""
    words = [w for w in text.split() if any(ch.isalpha() for ch in w)]
    if not words:
        return False
    # brackets and quotes around a word are ordinary punctuation; symbols left after removing them are not
    inside = sum(1 for w in words if any(ch in ";|{}[]~`^=" for ch in w.strip(".,:!?()[]{}\"'")))
    legacy = sum(1 for w in words if w.strip(".,;:!?()[]{}\"'") in _LEGACY_WORDS)
    # symbols alone could be code or maths, so they count when frequent or backed by at least one legacy word
    return (inside >= max(2, 0.1 * len(words)) and (legacy >= 1 or inside >= 0.25 * len(words))) or legacy >= max(3, 0.08 * len(words))


@_pdfium_serialized
def render_pdf_page(path: Path, page_index: int, dpi: int = 200) -> PageImage:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_index]
        w_pt, h_pt = page.get_size()
        scale = dpi / 72
        if max(w_pt, h_pt) * scale > MAX_SIDE:
            scale = MAX_SIDE / max(w_pt, h_pt)
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil()
        data, mime = _encode_image(img, prefer_png=True)
        size = img.size
        page.close()
    finally:
        pdf.close()
    return PageImage(page_index + 1, data, mime, size[0], size[1])


def parse_page_spec(spec: str | None, count: int) -> list[int]:
    """'1-3,5' -> [1,2,3,5] (1-based, clipped to the document). Ranges are clipped
    before they are expanded, so '1-1000000000' costs nothing."""
    if not spec or spec.strip().lower() in ("all", "*"):
        return list(range(1, count + 1))
    if len(spec) > 200 or spec.count(",") > 60:
        raise E.ToolError(E.INVALID_ARGUMENT, "Page spec is too long.", hint="Use ranges such as '1-30'.")
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                pages.update(range(max(a, 1), min(b, count) + 1))
            else:
                pages.add(int(part))
        except ValueError as exc:
            raise E.ToolError(E.INVALID_ARGUMENT, f"Bad page spec {spec!r}.", hint="Use forms like '1-3,5' or 'all'.") from exc
    valid = sorted(p for p in pages if 1 <= p <= count)
    if not valid:
        raise E.ToolError(E.INVALID_ARGUMENT, f"No pages of {count} match {spec!r}.", hint=f"Pages run from 1 to {count}.")
    return valid
