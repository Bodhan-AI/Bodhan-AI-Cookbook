"""Download real scanned pages for the stress tests: printed tables, old typefaces, two columns, handwriting.

    python demo/stress/fetch_real_pages.py

Sources, all reusable:
  * Census of India 1911, tables volume: public domain (published before 1929), on Wikimedia Commons.
  * "Ahimsa" by M. K. Gandhi, Hindi, 1911: public domain, on Wikimedia Commons.
  * A Tamil book nationalised by the Government of Tamil Nadu: CC0, released by the Tamil Virtual Academy, on Commons.
  * Gyan Kosh vol. 1, a Hindi encyclopaedia (1934): public domain in India, on Commons.
  * Bongabdo: full-page handwritten Bengali, CC BY 4.0, UCI Machine Learning Repository.

Only the pages needed are downloaded, not whole books: Wikimedia renders single PDF pages as
images, and for the Bongabdo zip (147 MB) only the needed files are read, using HTTP range requests.

These are real scans, so there is no "correct answer" file for them. The suite checks behaviour
(no crash, honest status, nothing invented) and reports how much text was read.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "bodhan-tool-agent-stress-tests/0.1 (Bodhan AI Cookbook recipe; demo data download)"}
BONGABDO_ZIP = "https://archive.ics.uci.edu/static/public/894/bongabdo.zip"

# case folder -> (Commons file title, pages to fetch, licence note)
COMMONS = {
    "r01_census_1911_tables": ("File:Census of India, 1911 .. (IA censusofindiav17pt1to2indi).pdf", [40], "Public domain (published 1913, before 1929)"),
    "r02_tamil_book_scan": ("File:வியாச விளக்கம்.pdf", [12, 13, 14], "CC0 1.0 (nationalised by the Government of Tamil Nadu, released by the Tamil Virtual Academy)"),
    "r03_hindi_1911_print": ("File:अहिंसा Ahimsa, by M. K. Gandhi.pdf", [10, 11], "Public domain (1911)"),
    "r04_hindi_encyclopedia_two_column": ("File:Gyan Kosh vol 1.pdf", [30], "Public domain in India (1934)"),
}


def commons_page(title: str, page: int, width: int = 1600) -> tuple[bytes, dict]:
    """One PDF page rendered as an image by Wikimedia, plus the file's licence and description page."""
    params = {
        "action": "query", "titles": title, "prop": "imageinfo", "format": "json",
        "iiprop": "url|extmetadata", "iiurlwidth": str(width), "iiurlparam": f"page{page}-{width}px",
    }
    data = requests.get(API, params=params, headers=HEADERS, timeout=60).json()
    info_pages = list(data.get("query", {}).get("pages", {}).values())
    if not info_pages or "imageinfo" not in info_pages[0]:
        raise RuntimeError(f"Wikimedia Commons has no file named {title!r}.")
    info = info_pages[0]["imageinfo"][0]
    meta = info.get("extmetadata", {})
    image = requests.get(info["thumburl"], headers=HEADERS, timeout=120)
    image.raise_for_status()
    return image.content, {
        "source": info.get("descriptionurl"),
        "licence": meta.get("LicenseShortName", {}).get("value"),
        "page": page,
    }


class RangeUnsupported(RuntimeError):
    """The server will not send part of a file."""


class HttpRangeFile(io.RawIOBase):
    """A read-only file over HTTP that fetches only the byte ranges actually read.

    zipfile reads the directory at the end of the archive, then each member it opens,
    so a few files can be taken from a large zip without downloading all of it.
    """

    def __init__(self, url: str):
        self.url, self.pos, self.fetched = url, 0, 0
        # Some servers do not advertise range support in HEAD, so ask for the first byte and check for 206.
        probe = requests.get(url, headers={**HEADERS, "Range": "bytes=0-0"}, allow_redirects=True, timeout=60, stream=True)
        probe.close()
        content_range = probe.headers.get("content-range", "")
        if probe.status_code != 206 or "/" not in content_range:
            raise RangeUnsupported()
        self.url = probe.url
        self.size = int(content_range.rsplit("/", 1)[1])

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.pos = {io.SEEK_SET: offset, io.SEEK_CUR: self.pos + offset, io.SEEK_END: self.size + offset}[whence]
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if self.pos >= self.size:
            return b""
        end = self.size - 1 if n is None or n < 0 else min(self.size - 1, self.pos + n - 1)
        resp = requests.get(self.url, headers={**HEADERS, "Range": f"bytes={self.pos}-{end}"}, timeout=120)
        if resp.status_code != 206:
            raise RuntimeError("The server ignored the partial-download request.")
        self.pos += len(resp.content)
        self.fetched += len(resp.content)
        return resp.content

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def _pick_pages(archive: zipfile.ZipFile, count: int, depth: int = 0) -> list[tuple[str, bytes]]:
    """Page images from the archive, looking inside zips stored within it and rendering PDFs if needed."""
    names = sorted(n for n in archive.namelist() if not n.endswith("/") and "__MACOSX" not in n)
    picked = [(Path(n).name, archive.read(n)) for n in names if n.lower().endswith(IMAGE_TYPES)][:count]
    for inner in (n for n in names if n.lower().endswith(".zip") and depth < 2):
        if len(picked) >= count:
            break
        with zipfile.ZipFile(io.BytesIO(archive.read(inner))) as nested:
            picked += _pick_pages(nested, count - len(picked), depth + 1)
    for pdf in (n for n in names if n.lower().endswith(".pdf")):
        if len(picked) >= count:
            break
        import pypdfium2

        doc = pypdfium2.PdfDocument(archive.read(pdf))
        image = doc[0].render(scale=200 / 72).to_pil().convert("RGB")
        out = io.BytesIO()
        image.save(out, "JPEG", quality=90)
        picked.append((Path(pdf).stem + ".jpg", out.getvalue()))
    if not picked and depth == 0:
        sample = ", ".join(names[:15]) or "(empty)"
        raise RuntimeError(f"No page images were found in the Bongabdo archive. It contains {len(names)} files, e.g. {sample}")
    return picked


def handwritten_pages(count: int = 2) -> tuple[list[tuple[str, bytes]], int]:
    """A few handwritten Bengali page images from the Bongabdo zip.

    Reads only the needed parts by byte range when the server allows it. Otherwise downloads the
    whole zip once (about 147 MB) into demo/stress/.downloads/ and reuses it on later runs.
    """
    try:
        remote = HttpRangeFile(BONGABDO_ZIP)
    except RangeUnsupported:
        local = HERE / ".downloads" / "bongabdo.zip"
        fetched = 0
        if not local.exists():
            print("  this server does not send partial files, so downloading the whole zip once (about 147 MB) ...")
            local.parent.mkdir(parents=True, exist_ok=True)
            partial = local.with_suffix(".part")
            with requests.get(BONGABDO_ZIP, headers=HEADERS, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(partial, "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
                        fetched += len(chunk)
            partial.replace(local)
        else:
            print("  using the zip downloaded earlier: demo/stress/.downloads/bongabdo.zip")
        with zipfile.ZipFile(local) as archive:
            return _pick_pages(archive, count), fetched
    with zipfile.ZipFile(io.BufferedReader(remote, buffer_size=1 << 16)) as archive:
        picked = _pick_pages(archive, count)
    return picked, remote.fetched


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sources: dict[str, list] = {}
    failures = []
    for case, (title, numbers, licence) in COMMONS.items():
        folder = INPUTS / case
        print(f"{case}: {title.removeprefix('File:')} ...")
        try:
            got = []
            for number in numbers:
                image, meta = commons_page(title, number)
                got.append((number, image, meta))
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
            failures.append(f"{case}: {exc}")
            print(f"  failed: {exc}")
            continue
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for number, image, _meta in got:
            (folder / f"page_{number:03d}.jpg").write_bytes(image)
            print(f"  inputs/{case}/page_{number:03d}.jpg")
        sources[case] = [{"title": title, "pages": numbers, "licence": licence, "licence_on_commons": got[0][2]["licence"], "url": got[0][2]["source"]}]

    case = "r05_handwritten_bengali"
    print(f"{case}: Bongabdo handwritten pages ...")
    try:
        pages, fetched = handwritten_pages()
        folder = INPUTS / case
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for name, data in pages:
            (folder / name).write_bytes(data)
            print(f"  inputs/{case}/{name}")
        print(f"  downloaded {fetched / 1e6:.1f} MB")
        sources[case] = [{"title": "Bongabdo (Ghosh, 2023), UCI Machine Learning Repository, doi:10.24432/C5XK7S", "licence": "CC BY 4.0", "url": "https://archive.ics.uci.edu/dataset/894/bongabdo", "files": [n for n, _ in pages]}]
    except (requests.RequestException, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        failures.append(f"{case}: {exc}")
        print(f"  failed: {exc}")

    path = HERE / "real_page_sources.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**existing, **sources}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(sources)} of {len(COMMONS) + 1} real-page cases. Sources and licences: demo/stress/real_page_sources.json")
    if failures:
        print("Some downloads failed; those cases will be skipped by the suite:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
