"""Translated on-screen text (slides, captions, lower thirds) with Indic-OCR.

English side (once per video, cached):  sample frames -> skip frames that look like the previous one -> Indic-OCR each
distinct frame -> keep real text blocks -> track each piece of text over time -> items {text, start, end, bbox, colours}.

Per language (only Indic-Translate writes Indian-language text):  translate every item, render the translation onto a
box that covers the original text, and burn it into the dubbed video for exactly the time the original was on screen.

Nothing here can fail a dub: any problem is logged, reported and the video is simply muxed without overlays.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, features

from . import textutil
from .logs import log

MAX_ITEMS = 40               # overlays are inputs to one ffmpeg command; the longest-lived, biggest text wins
MIN_SHOW_S = 1.0
SAME_FRAME_BITS = 8          # of 256 hash bits: at most this many differences = "same picture as before"
MIN_CONF = 0.4
SKIP_LABELS = ("footer", "page-number", "page_number", "picture", "figure", "image", "logo", "barcode", "formula", "code",
               "watermark", "table-of-contents")
SCRIPTS = {"hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "kok": "Devanagari", "mai": "Devanagari",
           "doi": "Devanagari", "brx": "Devanagari", "bn": "Bengali", "as": "Bengali", "mni": "Bengali", "ta": "Tamil", "te": "Telugu",
           "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati", "pa": "Gurmukhi", "or": "Oriya", "ur": "NaskhArabic",
           "sd": "NaskhArabic", "ks": "NaskhArabic", "sat": "OlChiki"}
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
             "C:/Windows/Fonts", "~/AppData/Local/Microsoft/Windows/Fonts")
# Windows ships Nirmala UI, which covers Devanagari, Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Odia, Ol Chiki
WINDOWS_INDIC_FONTS = ("Nirmala.ttc", "Nirmala.ttf", "NirmalaUI.ttf")


# ---------------------------------------------------------------- frames
def sample_frames(video: Path, duration: float, interval: float, max_frames: int, out: Path) -> tuple[list[tuple[float, Path]], float]:
    """One PNG per `interval` seconds, taken in the middle of its interval, at most 1280 px wide."""
    iv = max(float(interval), duration / max(1, max_frames), 0.5)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{iv / 2:.3f}", "-i", str(video), "-an", "-t", f"{duration:.3f}",
           "-vf", f"fps=1/{iv:.4f},scale='min(1280,iw)':-2", "-start_number", "0", str(out / "f%04d.png")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg frame sampling failed: {r.stderr[-300:]}")
    files = sorted(out.glob("f*.png"))[:max_frames]
    return [(k * iv, f) for k, f in enumerate(files)], iv


def ahash(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        a = np.asarray(im.convert("L").resize((16, 16), Image.BILINEAR), dtype=np.float32)
    return (a > a.mean()).ravel()


# ---------------------------------------------------------------- text blocks
def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    lines = []
    for ln in s.splitlines():
        ln = re.sub(r"^\s*(#{1,6}|>|[-*+]|\d{1,2}[.)])\s+", "", ln.strip())
        ln = re.sub(r"(\*\*|__|`)", "", ln).strip()
        if ln:
            lines.append(ln)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def block_reason(b: dict) -> str | None:
    """Why a block is not translated (None = keep)."""
    label = (b.get("label") or "").lower()
    raw = b.get("text") or ""
    if any(w in label for w in SKIP_LABELS):
        return "furniture_or_picture"
    if not raw.strip():
        return "no_text"
    if b.get("conf", 1) < MIN_CONF:
        return "low_confidence"
    if "<table" in raw.lower() or "|---" in raw:
        return "table_not_translated"
    t = clean_text(raw)
    if t.startswith("$") or "\\(" in t or "\\frac" in t:
        return "formula"
    if re.search(r"[؀-ۿऀ-෿]", t):
        return "already_not_english"
    if len(re.findall(r"[A-Za-z]", t)) < 2:
        return "no_letters"
    if len(t) > 400:
        return "too_long"
    return None


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def lum(c) -> float:
    def f(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def contrast(a, b) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def colours(png: Path, bbox) -> tuple[list[int], list[int]]:
    """Background = median colour of the text box, text colour = the pixels farthest from it (or black/white if low contrast)."""
    with Image.open(png) as im:
        a = np.asarray(im.convert("RGB"))
    h, w = a.shape[:2]
    x0, y0, x1, y1 = int(bbox[0] * w), int(bbox[1] * h), max(int(bbox[2] * w), int(bbox[0] * w) + 1), max(int(bbox[3] * h), int(bbox[1] * h) + 1)
    reg = a[y0:y1, x0:x1].reshape(-1, 3).astype(np.int32)
    if reg.size == 0:
        return [255, 255, 255], [0, 0, 0]
    bg = np.median(reg, axis=0)
    d = np.abs(reg - bg).sum(axis=1)
    far = reg[d >= np.percentile(d, 90)] if len(reg) > 10 else reg
    fg = far.mean(axis=0) if len(far) else bg
    bg_l, fg_l = [int(v) for v in bg], [int(v) for v in fg]
    if contrast(bg_l, fg_l) < 3.0:
        fg_l = [255, 255, 255] if lum(bg_l) < 0.4 else [17, 17, 17]
    return bg_l, fg_l


# ---------------------------------------------------------------- extraction (English side, cached)
def extract(video: Path, client, work: Path, duration: float, sha: str, interval: float = 2.0, max_frames: int = 40,
            concurrency: int = 2) -> dict:
    key = {"sha": sha, "interval": interval, "max_frames": max_frames, "v": 1}
    cache = work / "onscreen.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            if c.get("key") == key:
                log("stage", stage="onscreen", cached=True, items=len(c["items"]))
                return c
        except ValueError:
            pass
    frames, iv = sample_frames(video, duration, interval, max_frames, work / "onscreen_frames")
    log("stage", stage="onscreen_frames", frames=len(frames), interval=round(iv, 2))
    hashes = [ahash(p) for _, p in frames]
    need = [i for i in range(len(frames)) if i == 0 or int((hashes[i] != hashes[i - 1]).sum()) > SAME_FRAME_BITS]
    warnings, errors = [], []

    def do(i):
        try:
            return i, client.ocr(frames[i][1].read_bytes()), None
        except Exception as e:           # one bad frame must not lose the others
            return i, None, e

    with ThreadPoolExecutor(max(1, concurrency)) as ex:
        got = {i: (r, e) for i, r, e in ex.map(do, need)}
    errors = [str(e)[:200] for r, e in got.values() if e]
    if errors and len(errors) == len(got):
        raise RuntimeError(f"Indic-OCR failed on every frame: {errors[0]}")
    if errors:
        warnings.append(f"{len(errors)} of {len(got)} frames could not be read by Indic-OCR: {errors[0]}")
    last = None
    res = {}
    for i in range(len(frames)):
        if i in got:
            last = got[i][0]               # None when this frame failed: treated as no text
        res[i] = last
    skipped: dict[str, int] = {}
    open_items: list[dict] = []
    done: list[dict] = []
    sizes = {}
    for i, (_t, path) in enumerate(frames):
        with Image.open(path) as im:
            W, H = im.size
        sizes[i] = (W, H)
        cands = []
        for b in (res[i] or {"blocks": []})["blocks"]:
            why = block_reason(b)
            if why:
                if i in got and b.get("text", "").strip():
                    skipped[why] = skipped.get(why, 0) + 1
                continue
            bb = b["bbox"]
            frac = [max(0.0, min(1.0, bb[0] / W)), max(0.0, min(1.0, bb[1] / H)), max(0.0, min(1.0, bb[2] / W)), max(0.0, min(1.0, bb[3] / H))]
            if frac[2] - frac[0] < 0.01 or frac[3] - frac[1] < 0.01:
                continue
            cands.append({"text": clean_text(b["text"]), "bbox": frac, "label": b["label"], "conf": b["conf"]})
        matched = set()
        for c in cands:
            best, bs = None, 0.0
            for k, it in enumerate(open_items):
                if k in matched:
                    continue
                s = difflib.SequenceMatcher(None, it["text"].lower(), c["text"].lower()).ratio()
                if s >= 0.85 and iou(it["bbox"], c["bbox"]) >= 0.3 and s > bs:
                    best, bs = k, s
            if best is None:
                open_items.append({**c, "first": i, "last": i, "frame0": str(path)})
                matched.add(len(open_items) - 1)
            else:
                open_items[best].update(last=i, bbox=c["bbox"])
                matched.add(best)
        keep = []
        for k, it in enumerate(open_items):
            (keep if k in matched else done).append(it)
        open_items = keep
    done += open_items
    items = []
    for it in done:
        start = max(0.0, it["first"] * iv)
        end = min(duration, (it["last"] + 1) * iv)
        if end - start < MIN_SHOW_S:
            end = min(duration, start + MIN_SHOW_S)
        items.append({**{k: it[k] for k in ("text", "bbox", "label", "conf")}, "start": round(start, 2), "end": round(end, 2),
                      "frame0": it["frame0"]})
    area = lambda x: (x["bbox"][2] - x["bbox"][0]) * (x["bbox"][3] - x["bbox"][1])   # noqa: E731
    items.sort(key=lambda x: -((x["end"] - x["start"]) * area(x)))
    if len(items) > MAX_ITEMS:
        skipped["over_limit"] = len(items) - MAX_ITEMS
        items = items[:MAX_ITEMS]
    items.sort(key=lambda x: (x["start"], x["bbox"][1]))
    for k, it in enumerate(items):
        it["id"] = k
        it["bg"], it["fg"] = colours(Path(it.pop("frame0")), it["bbox"])
    out = {"key": key, "frames": len(frames), "ocr_calls": len(need), "interval": round(iv, 2), "items": items,
           "skipped": skipped, "warnings": warnings}
    textutil.atomic_write(cache, json.dumps(out, ensure_ascii=False, indent=1))
    shutil.rmtree(work / "onscreen_frames", ignore_errors=True)
    log("stage", stage="onscreen", frames=len(frames), ocr_calls=len(need), items=len(items), skipped=sum(skipped.values()))
    return out


# ---------------------------------------------------------------- rendering (per language)
def find_font(lang: str) -> tuple[str | None, str]:
    """A font file that covers the language's script. Returns (path, note). Noto Sans <Script> is preferred."""
    import os
    script = SCRIPTS.get(lang, "")
    names = [f"NotoSans{script}-Regular.ttf", f"NotoSans{script}-Regular.otf", f"NotoSans{script}[wdth,wght].ttf",
             f"NotoSans{script}[wght].ttf", f"Noto{script}-Regular.ttf"] if script else []
    roots = [os.environ.get("BODHAN_DUB_FONT_DIR", ""), os.path.join(os.environ.get("WINDIR", ""), "Fonts") if os.environ.get("WINDIR") else "",
             *[os.path.expanduser(d) for d in FONT_DIRS]]
    for root in roots:
        if not root or not Path(root).exists():
            continue
        for n in names:
            hit = next(iter(Path(root).rglob(n)), None)
            if hit:
                return str(hit), "noto"
    for root in roots:                                    # Windows built-in Indic font
        for n in WINDOWS_INDIC_FONTS:
            if root and (Path(root) / n).exists() and script not in ("NaskhArabic", "OlChiki", ""):
                return str(Path(root) / n), "windows_nirmala"
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}", f":lang={lang}"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip() and Path(r.stdout.strip()).exists():
            return r.stdout.strip(), "fontconfig"
    except (OSError, subprocess.SubprocessError):
        pass
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            return p, "fallback_may_lack_glyphs"
    return None, "no_font"


class _Contours:
    """fontTools-style pen that flattens glyph outlines into polygons (curves become short line pieces)."""

    def __init__(self):
        self.done, self.cur = [], []

    def moveTo(self, p):
        self.cur = [p]

    def lineTo(self, p):
        self.cur.append(p)

    def curveTo(self, *pts):                       # cubic Bezier(s): control, control, end
        for i in range(0, len(pts) - 2, 3):
            p0, (p1, p2, p3) = self.cur[-1], pts[i:i + 3]
            for k in range(1, 9):
                t = k / 8
                u = 1 - t
                self.cur.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                                 u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))

    def qCurveTo(self, *pts):                      # TrueType: any number of off-curve points, implied on-curve points between them
        pts = [q for q in pts if q is not None]
        offs, end = pts[:-1], pts[-1]
        p0 = self.cur[-1]
        for i, c in enumerate(offs):
            nxt = end if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
            for k in range(1, 7):
                t = k / 6
                u = 1 - t
                self.cur.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * nxt[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * nxt[1]))
            p0 = nxt

    def closePath(self):
        if len(self.cur) > 2:
            self.done.append(self.cur)
        self.cur = []

    endPath = closePath


def _area(poly) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2


class ShapedFont:
    """Indic text without Raqm. HarfBuzz (the uharfbuzz package, a plain pip wheel on Windows, macOS and Linux) shapes the line
    into the right glyphs, so conjuncts, matras and reordering are correct; the glyph outlines are then drawn with Pillow.
    Same getlength()/size interface as a Pillow font, so wrapping and sizing code does not care which one it has."""
    SS = 3                                          # supersampling for smooth edges

    def __init__(self, path: str, size: int):
        import uharfbuzz as hb
        self.hb, self.size = hb, size
        self.face = hb.Face(hb.Blob.from_file_path(path))
        self.font = hb.Font(self.face)
        self.scale = size / self.face.upem
        ext = self.font.get_font_extents("ltr")
        self.ascent, self.descent = ext.ascender * self.scale, -ext.descender * self.scale
        self._glyphs: dict[int, list] = {}

    def shape(self, text: str):
        buf = self.hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        self.hb.shape(self.font, buf, {"kern": True, "liga": True, "ccmp": True, "locl": True})
        return buf.glyph_infos, buf.glyph_positions

    def getlength(self, text: str) -> float:
        return sum(p.x_advance for p in self.shape(text)[1]) * self.scale

    def _outline(self, gid: int) -> list:
        if gid not in self._glyphs:
            pen = _Contours()
            self.font.draw_glyph_with_pen(gid, pen)
            self._glyphs[gid] = pen.done
        return self._glyphs[gid]

    def mask(self, text: str) -> tuple[Image.Image, int]:
        """Greyscale coverage image of one line. Returns (mask, baseline y inside the mask)."""
        infos, poss = self.shape(text)
        pad = 2
        w = int(sum(p.x_advance for p in poss) * self.scale) + 2 * pad + int(self.size * 0.3)
        h = int(self.ascent + self.descent) + 2 * pad + int(self.size * 0.2)
        base = int(self.ascent) + pad
        ss = self.SS
        out = Image.new("L", (w * ss, h * ss), 0)
        x = float(pad)
        for info, pos in zip(infos, poss):
            polys = self._outline(info.codepoint)
            if polys:
                ox, oy = x + pos.x_offset * self.scale, base - pos.y_offset * self.scale
                wind = np.zeros((out.size[1], out.size[0]), dtype=np.int16)    # nonzero winding rule: overlaps add ink, opposite turns cut holes
                for q in polys:
                    pts = [((ox + px * self.scale) * ss, (oy - py * self.scale) * ss) for px, py in q]
                    m = Image.new("L", out.size, 0)
                    ImageDraw.Draw(m).polygon(pts, fill=1)
                    wind += (np.asarray(m, dtype=np.int16) * (1 if _area(q) > 0 else -1))
                out = ImageChops.lighter(out, Image.fromarray(((wind != 0) * 255).astype(np.uint8), "L"))
            x += pos.x_advance * self.scale
        return out.resize((w, h), Image.LANCZOS), base

    def draw(self, img: Image.Image, xy: tuple[float, float], text: str, fill) -> None:
        m, _ = self.mask(text)
        img.paste(Image.new("RGBA", m.size, tuple(fill)), (int(xy[0]), int(xy[1] + (self.size * 1.0 - self.ascent))), m)


def have_harfbuzz() -> bool:
    try:
        import uharfbuzz  # noqa: F401
        return True
    except ImportError:
        return False


def load_font(path: str | None, size: int):
    """Best available text engine: Raqm (if Pillow has it), else HarfBuzz via uharfbuzz, else Pillow's basic layout,
    which draws Indic characters but cannot form conjuncts."""
    if not path:
        return ImageFont.load_default()
    if features.check("raqm"):
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    if have_harfbuzz():
        try:
            return ShapedFont(path, size)
        except Exception as e:                       # unreadable font file or .ttc collection: fall through to Pillow
            log("font_shaping_unavailable", error=str(e)[:200])
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def draw_line(img: Image.Image, d, xy: tuple[float, float], text: str, font, fill) -> None:
    if isinstance(font, ShapedFont):
        font.draw(img, xy, text, fill)
    else:
        d.text(xy, text, font=font, fill=fill)


def wrap(text: str, font, max_w: float) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if cur and font.getlength(t) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines or [""]


def render_overlay(item: dict, text: str, font_path: str | None, W: int, H: int, out_png: Path) -> dict:
    """Box over the original text, translated text inside. Font size follows the original line height and shrinks to fit
    before the box is allowed to grow, so the translation stays near where the viewer was already looking."""
    x0, y0, x1, y1 = item["bbox"][0] * W, item["bbox"][1] * H, item["bbox"][2] * W, item["bbox"][3] * H
    bw, bh = max(8.0, x1 - x0), max(8.0, y1 - y0)
    pad = max(4, int(min(bw, bh) * 0.12))
    n = max(1, len(item["text"]))
    size = int(min(96, max(16, (bw * bh / (0.65 * n)) ** 0.5 * 0.95)))      # em size implied by the box area and the character count
    avail = min(max(bw * 1.15, 0.28 * W), W - 8) - 2 * pad
    while True:
        font = load_font(font_path, size)
        lines = wrap(text, font, avail)
        lh = int(size * 1.3)
        if (len(lines) * lh <= max(bh * 1.25, lh) or size <= 16):
            break
        size = int(size * 0.92)
    tw = int(max(font.getlength(ln) for ln in lines)) + 2 * pad
    th = len(lines) * lh + 2 * pad
    cw, ch = int(max(bw + 2 * pad, tw)), int(max(bh + 2 * pad, th))
    cw, ch = min(cw, W), min(ch, H)
    x = int(min(max(0, x0 - pad), W - cw))
    y = int(min(max(0, y0 - pad), H - ch))
    img = Image.new("RGBA", (cw, ch), (*item["bg"], 255))
    d = ImageDraw.Draw(img)
    cx = x0 + bw / 2
    centered = abs(cx / W - 0.5) < 0.04 and bw > 0.2 * W
    for k, ln in enumerate(lines):
        lw = font.getlength(ln)
        tx = (cw - lw) / 2 if centered else pad
        draw_line(img, d, (tx, pad + k * lh), ln, font, (*item["fg"], 255))
    img.save(out_png)
    return {"png": str(out_png), "x": x, "y": y, "w": cw, "h": ch, "font_size": size, "lines": len(lines),
            "overflow": bool(len(lines) * lh > bh * 1.25 and size <= 16)}


def video_size(video: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                        str(video)], capture_output=True, text=True)
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


def mux(video: Path, mix_wav: Path, out: Path, duration: float, overlays: list[dict]) -> None:
    """Dubbed audio + original picture + one timed overlay per translated text. Re-encodes the video (needed to draw on it)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(mix_wav)]
    for o in overlays:
        cmd += ["-i", o["png"]]
    parts, prev = [], "0:v"
    for k, o in enumerate(overlays):
        parts.append(f"[{prev}][{k + 2}:v]overlay=x={o['x']}:y={o['y']}:format=auto:enable='between(t,{o['start']:.3f},{o['end']:.3f})'[v{k}]")
        prev = f"v{k}"
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{prev}]", "-map", "1:a:0", "-t", f"{duration:.3f}", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg overlay failed: {r.stderr[-400:]}")
