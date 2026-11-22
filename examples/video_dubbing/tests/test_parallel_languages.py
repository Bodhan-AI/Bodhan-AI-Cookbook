"""Languages run in parallel: translate + dub overlap, mixing/verifying never overlap, and a failed language
never stops the others. Every stage is faked; no audio, no API."""

import threading
import time

from bodhan_dub import pipeline as P


def _fake_stages(monkeypatch, tmp_path, fail=("ta",)):
    lock = threading.Lock()
    state = {"dub": 0, "dub_max": 0, "heavy": 0, "heavy_max": 0, "dubbed": []}

    def enter(key):
        with lock:
            state[key] += 1
            state[key + "_max"] = max(state[key + "_max"], state[key])

    def leave(key):
        with lock:
            state[key] -= 1

    def translate_units(units, lang, work, **kw):
        if lang in fail:
            raise RuntimeError(f"{lang} broke")
        return [{"id": 1}]

    def dub_language(segs, total, work, cfg):
        enter("dub")
        time.sleep(0.3)
        state["dubbed"].append(cfg.lang)
        leave("dub")
        return [], {}

    def assemble(recs, segs, video, mp4, asm_dir, **kw):
        enter("heavy")
        time.sleep(0.15)
        asm_dir.mkdir(parents=True, exist_ok=True)
        (asm_dir / "subtitles.srt").write_text("", encoding="utf-8")
        mp4.parent.mkdir(parents=True, exist_ok=True)
        leave("heavy")
        return {"speech_track": None, "final_audio": None}

    def measure_sync(*a, **k):
        enter("heavy")
        time.sleep(0.05)
        leave("heavy")
        return {}

    fakes = {
        "probe_media": lambda v: {"has_audio": True, "duration": 10.0},
        "extract_audio": lambda *a, **k: tmp_path / "a.wav",
        "chunk_audio": lambda *a, **k: [], "transcribe_chunks": lambda *a, **k: [],
        "punctuate_chunks": lambda *a, **k: [], "build_sentences": lambda *a, **k: [],
        "review_boundaries": lambda *a, **k: ([], []),
        "split_sentences": lambda s: [{"id": 1, "start": 0.0, "end": 1.0, "text": "x"}],
        "source_regions_from_chunks": lambda raw: [], "snap_stats": lambda p: {},
        "translate_units": translate_units, "dub_language": dub_language, "assemble": assemble,
        "measure_sync": measure_sync, "verify_mux": lambda *a, **k: {},
        "_lang_report": lambda lang, *a: {"status": "ok"}, "_segment_out": lambda s, r, *a: {},
        "_targets": lambda rep: {}, "_cost": lambda *a: {},
    }
    for name, f in fakes.items():
        monkeypatch.setattr(P, name, f)
    return state


def test_languages_overlap_but_mixing_takes_turns(tmp_path, monkeypatch):
    state = _fake_stages(monkeypatch, tmp_path)
    rep = P.run(tmp_path / "v.mp4", ["hi", "ta", "bn"], tmp_path / "out", separation="off", align=False)
    assert state["dub_max"] == 2                     # hi and bn dubbed at the same time (ta failed early)
    assert state["heavy_max"] == 1                   # mixing / verifying never overlap
    assert list(rep["per_language"]) == ["hi", "ta", "bn"]
    assert rep["per_language"]["ta"]["status"] == "failed" and "ta broke" in rep["per_language"]["ta"]["error"]
    assert rep["per_language"]["hi"]["status"] == rep["per_language"]["bn"]["status"] == "ok"
    assert "languages_wall" in rep["timings_s"]


def test_one_worker_runs_languages_one_after_another(tmp_path, monkeypatch):
    state = _fake_stages(monkeypatch, tmp_path, fail=())
    rep = P.run(tmp_path / "v.mp4", ["hi", "ta", "hi"], tmp_path / "out", separation="off", align=False,
                lang_workers=1)
    assert state["dub_max"] == 1 and sorted(state["dubbed"]) == ["hi", "ta"]   # duplicate "hi" runs once
    assert rep["languages"] == ["hi", "ta"]
