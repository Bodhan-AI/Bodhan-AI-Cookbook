"""Record the demo walkthrough with Playwright.

This targets the artefacts that actually exist in this recipe:

  * docs/site/index.html            the project page
  * docs/sample_report/summary.md   a real report from a real run

It does **not** drive a "Control Center", a "Live Studio" tab or a "Review Queue" —
none of those exist in this harness. If a script mentions `#live-source` or a
`Quality Analytics` button, it was written for a different project.

Usage
-----
    pip install playwright
    python -m playwright install chromium        # one-time, ~150 MB

    # record the static walkthrough (no server needed)
    python tools/record_demo.py --out artifacts/video

    # or record a live run first, then walk the report it produced
    python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report --offline
    python tools/record_demo.py --out artifacts/video --report ./report

Output is a .webm in ``artifacts/video``. Convert to .mp4 with:
    ffmpeg -i recording.webm -c:v libx264 -crf 23 -pix_fmt yuv420p demo.mp4

If Playwright cannot download its browser, pass a system Chromium:
    python tools/record_demo.py --chromium /usr/bin/chromium
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _file_url(path: Path) -> str:
    return path.resolve().as_uri()


async def record(out: Path, report: Path | None, chromium: str | None,
                 width: int, height: int, slow: float) -> Path:
    from playwright.async_api import async_playwright  # imported late: optional dep

    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.webm"):
        stale.unlink()

    launch: dict = {"headless": True}
    if chromium:
        launch["executable_path"] = chromium

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            record_video_dir=str(out),
            record_video_size={"width": width, "height": height},
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        page = await context.new_page()

        # 1 — the project page: hero, architecture, checks, results
        site = ROOT / "docs" / "site" / "index.html"
        await page.goto(_file_url(site))
        await page.wait_for_timeout(int(2500 * slow))
        for _ in range(4):
            await page.mouse.wheel(0, 760)
            await page.wait_for_timeout(int(1800 * slow))

        # 2 — the report, if a live run produced one
        target = report or (ROOT / "docs" / "sample_report")
        summary = target / "summary.md"
        if summary.exists():
            await page.goto(_file_url(summary))
            await page.wait_for_timeout(int(2000 * slow))
            for _ in range(5):
                await page.mouse.wheel(0, 620)
                await page.wait_for_timeout(int(1600 * slow))

        await context.close()
        await browser.close()

    videos = sorted(out.glob("*.webm"))
    if not videos:
        raise SystemExit("no video produced — check that Playwright recorded")
    final = out / "demo_walkthrough.webm"
    shutil.move(str(videos[-1]), final)
    return final


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("artifacts/video"))
    ap.add_argument("--report", type=Path, default=None,
                    help="a report directory from a real run (defaults to docs/sample_report)")
    ap.add_argument("--chromium", default=None,
                    help="path to a system chromium, if Playwright cannot fetch its own")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--slow", type=float, default=1.0, help="pace multiplier for the pauses")
    args = ap.parse_args()

    final = asyncio.run(record(args.out, args.report, args.chromium,
                               args.width, args.height, args.slow))
    print(f"wrote {final}")
    print("convert with: ffmpeg -i %s -c:v libx264 -crf 23 -pix_fmt yuv420p demo.mp4" % final)


if __name__ == "__main__":
    main()
