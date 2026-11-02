"""Live smoke test: one short sentence through translate + speak for every language.

Costs about Rs 1-2. Saves one WAV per language in outputs/smoke/ and a results
table in outputs/smoke_results.md. Run it from the project folder:

    python smoke_test.py            # first voice of each language (23 runs)
    python smoke_test.py --both     # both voices of each language (46 speak calls)
    python smoke_test.py hi mr ta   # only the languages you name
"""
import sys
import time
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from narrator import config
from narrator.bodhan_client import BodhanClient
from narrator.errors import NarratorError

ENGLISH = "The library will be closed on Friday. Please return your books."
HINDI = "पुस्तकालय शुक्रवार को बंद रहेगा। कृपया अपनी पुस्तकें लौटा दें।"  # source for target "en"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
both = "--both" in sys.argv
codes = args or list(config.LANGUAGES)

out_dir = Path("outputs/smoke")
out_dir.mkdir(parents=True, exist_ok=True)
client = BodhanClient()
rows = []

for code in codes:
    if code not in config.LANGUAGES:
        print(f"Skipping unknown language code: {code}")
        continue
    voices = config.VOICES[code] if both else config.VOICES[code][:1]
    started = time.monotonic()
    try:
        translation, truncated = client.translate(HINDI if code == "en" else ENGLISH, code)
        if truncated or not translation.strip():
            raise NarratorError("translation was empty or cut off")
        for voice in voices:
            wav = client.speak(translation, voice, code)
            with wave.open(BytesIO(wav)) as clip:
                seconds = clip.getnframes() / clip.getframerate()
            (out_dir / f"{code}_{voice}.wav").write_bytes(wav)
            status = "PASS"
            rows.append((code, voice, status, f"{seconds:.1f}", f"{time.monotonic() - started:.0f}", translation))
            print(f"PASS {code:4} {voice:10} audio {seconds:4.1f}s  {translation[:40]}")
            started = time.monotonic()
    except Exception as problem:  # report every failure and keep going
        rows.append((code, "-", "FAIL", "-", "-", f"{type(problem).__name__}: {problem}"))
        print(f"FAIL {code:4} {type(problem).__name__}: {problem}")
    time.sleep(2)  # be gentle with the rate limit

passed = sum(1 for r in rows if r[2] == "PASS")
lines = [
    f"# Smoke test, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
    "",
    "| Code | Language | Voice | Result | Audio (s) | Wall time (s) |",
    "| --- | --- | --- | --- | --- | --- |",
]
for code, voice, status, audio, wall, _ in rows:
    lines.append(f"| {code} | {config.LANGUAGES[code]} | {voice} | {status} | {audio} | {wall} |")
Path("outputs/smoke_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\n{passed} of {len(rows)} passed. Table saved to outputs/smoke_results.md")
