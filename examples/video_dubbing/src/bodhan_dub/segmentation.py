"""Speech segmentation: silence-based chunks (<=30 s STT window) and sentence grouping.

Indic-Transcribe returns text only, so segment timestamps come from voice-activity detection:
we cut at pauses, never inside continuous speech unless a stretch exceeds the window, and then
we regroup transcribed chunks into whole sentences before translation.
"""
import re

SENT_END = re.compile(r"[.!?।॥…][\"')\]]*\s*$")


def parse_silences(stderr: str, duration: float) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        out.append((max(0.0, s), min(duration, e)))
    return out


def adaptive_threshold(db: list[float], margin_db: float = 6.0, dyn_db: float = 25.0, abs_floor_db: float = -55.0) -> float:
    """Speech threshold relative to this clip. Normally within `dyn_db` of the loud speech. If there is a clearly
    separated noise or music floor (>= 15 dB below the speech), sit just above it instead. If there is no such
    floor (continuous speech), the percentile "floor" is just speech, so it is ignored."""
    s = sorted(db)
    floor, peak = s[int(0.10 * (len(s) - 1))], s[int(0.97 * (len(s) - 1))]
    thr = peak - dyn_db
    if peak - floor >= 15 and floor + margin_db > thr:
        thr = floor + margin_db
    return max(thr, abs_floor_db)


def span_is_steady(db_frames: list[float], min_range_db: float = 6.0, min_frames: int = 25) -> bool:
    """Speech has syllable-level energy swings; a constant music bed or hum does not."""
    if len(db_frames) < min_frames:
        return False
    s = sorted(db_frames)
    return s[int(0.9 * (len(s) - 1))] - s[int(0.1 * (len(s) - 1))] < min_range_db


def implausible_transcript(text: str, seconds: float) -> str | None:
    """Reasons an ASR output over a stretch of audio is probably hallucinated (music, noise)."""
    words = text.split()
    if seconds > 0 and len(words) / seconds > 7.5:
        return "too_many_words_for_duration"
    if len(words) >= 8 and len(set(w.lower() for w in words)) / len(words) < 0.3:
        return "degenerate_repetition"
    return None


def energy_spans(db: list[float], hop: float, min_gap: float = 0.25, min_speech: float = 0.12):
    """Speech spans from frame energies (dB). A constant music bed sits under the threshold, so it stays non-speech."""
    if not db:
        return []
    thr = adaptive_threshold(db)
    sm = [sorted(db[max(0, i - 2):i + 3])[len(db[max(0, i - 2):i + 3]) // 2] for i in range(len(db))]  # 5-frame median
    spans, start = [], None
    for i, v in enumerate(sm):
        if v > thr and start is None:
            start = i
        elif v <= thr and start is not None:
            spans.append([start * hop, i * hop])
            start = None
    if start is not None:
        spans.append([start * hop, len(db) * hop])
    merged = []
    for s, e in spans:
        if merged and s - merged[-1][1] < min_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    out = []
    for s, e in merged:
        if e - s < min_speech:
            continue
        if span_is_steady(db[int(s / hop):int(e / hop)]):
            continue
        out.append((s, e))
    return out


def speech_spans(silences: list[tuple[float, float]], duration: float, min_speech: float = 0.12):
    spans, cur = [], 0.0
    for s, e in sorted(silences):
        if s - cur >= min_speech:
            spans.append((cur, s))
        cur = max(cur, e)
    if duration - cur >= min_speech:
        spans.append((cur, duration))
    return spans


def _split_long(spans, max_len, soft_len=0.0, soft_gap=0.3):
    """Recursively split a run of spans at its largest internal pause until each part fits.
    Beyond `soft_len` seconds a run is also split at a real pause (>= soft_gap) so no single "sentence" is huge."""
    start, end = spans[0][0], spans[-1][1]
    if len(spans) > 1 and soft_len and end - start > soft_len:
        gaps = [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]
        k = max(range(len(gaps)), key=lambda i: gaps[i])
        if gaps[k] >= soft_gap:
            return _split_long(spans[:k + 1], max_len, soft_len, soft_gap) + _split_long(spans[k + 1:], max_len, soft_len, soft_gap)
    if end - start <= max_len:
        return [(spans, False)]
    if len(spans) == 1:  # continuous speech longer than the window: unavoidable hard split
        n = int((end - start) // max_len) + 1
        step = (end - start) / n
        return [([(start + k * step, start + (k + 1) * step)], True) for k in range(n)]
    gaps = [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]
    k = max(range(len(gaps)), key=lambda i: gaps[i])
    return _split_long(spans[:k + 1], max_len, soft_len, soft_gap) + _split_long(spans[k + 1:], max_len, soft_len, soft_gap)


def make_chunks(spans, duration, max_len=25.0, cut_gap=0.6, min_len=0.35, pad=0.08, soft_len=14.0):
    """Return (chunks, skipped). A chunk is {start, end, hard_split}."""
    runs, cur = [], []
    for sp in spans:
        if cur and sp[0] - cur[-1][1] >= cut_gap:
            runs.append(cur)
            cur = []
        cur.append(sp)
    if cur:
        runs.append(cur)
    parts = []
    for r in runs:
        parts += _split_long(r, max_len, soft_len)
    raw = [(p[0][0], p[-1][1], hard) for p, hard in parts]
    chunks, skipped = [], []
    for i, (s, e, hard) in enumerate(raw):
        prev_end = raw[i - 1][1] if i else 0.0
        next_start = raw[i + 1][0] if i + 1 < len(raw) else duration
        s2 = max(s - pad, (prev_end + s) / 2 if i else 0.0)
        e2 = min(e + pad, (e + next_start) / 2 if i + 1 < len(raw) else duration)
        if e - s < min_len:
            skipped.append({"start": round(s, 3), "end": round(e, 3), "reason": "too_short_noise"})
            continue
        chunks.append({"start": round(s2, 3), "end": round(e2, 3), "hard_split": hard})
    return chunks, skipped


def heuristic_groups(texts: list[str], chunks: list[dict], max_span: float = 15.0, max_gap: float = 2.0):
    """Group consecutive chunks into sentences using terminal punctuation, never letting a group exceed ~max_span seconds
    (STT output often has little punctuation). Fallback when the LLM is unavailable."""
    groups, cur = [], []
    for i, t in enumerate(texts):
        if cur and chunks[i]["end"] - chunks[cur[0]]["start"] > max_span:
            groups.append(cur)
            cur = []
        cur.append(i)
        gap = chunks[i + 1]["start"] - chunks[i]["end"] if i + 1 < len(texts) else 99
        if SENT_END.search(t.strip()) or gap > max_gap:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def valid_groups(groups, n: int, chunks: list[dict], max_span: float = 24.0) -> bool:
    """Groups must partition 0..n-1 in order, and a group may not be too long to speak in one go."""
    flat = [i for g in groups if isinstance(g, list) for i in g]
    if flat != list(range(n)) or any(not isinstance(g, list) or not g for g in groups):
        return False
    return all(chunks[g[-1]]["end"] - chunks[g[0]]["start"] <= max_span for g in groups)


def build_segments(texts: list[str], chunks: list[dict], groups: list[list[int]]) -> list[dict]:
    segs = []
    for k, g in enumerate(groups):
        segs.append({
            "idx": k, "start": chunks[g[0]]["start"], "end": chunks[g[-1]]["end"], "chunk_ids": g,
            "text": " ".join(texts[i].strip() for i in g).strip(),
        })
    return segs


def gaps_for(segs: list[dict], duration: float, margin: float = 0.05):
    """For each segment: silence available before and after it (to the neighbouring segment)."""
    out = []
    for i, s in enumerate(segs):
        before = s["start"] - (segs[i - 1]["end"] if i else 0.0)
        after = (segs[i + 1]["start"] if i + 1 < len(segs) else duration) - s["end"]
        out.append((max(0.0, before - margin), max(0.0, after - margin)))
    return out
