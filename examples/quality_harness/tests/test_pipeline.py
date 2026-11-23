"""End-to-end: the CLI on a small dataset against a fake Indic-Translate, twice."""

import copy
import json

import httpx2
import pytest

import bodhan_eval.cli as cli
from bodhan_eval.clients import Clients
from test_clients import TRANSLATE_OK

SRC = "The museum opens at 10 am. Entry costs 50 rupees for adults."
HI = "संग्रहालय सुबह 10 बजे खुलता है। वयस्कों के लिए प्रवेश शुल्क 50 रुपये है।"
MD = "## Results\n\n| Metric | Value |\n|---|---|\n| Accuracy | 92% |"
TA = "## முடிவுகள்\n\n| அளவீடு | மதிப்பு |\n|---|---|\n| துல்லியம் | 92% |"

# The fake translator: forward translations by source, back-translations phrase by phrase.
FORWARD = {(SRC, "hi"): HI, (MD, "ta"): TA}
BACK = {
    "संग्रहालय सुबह 10 बजे खुलता है।": "The museum opens at 10 am.",
    "वयस्कों के लिए प्रवेश शुल्क 50 रुपये है।": "Entry costs 50 rupees for adults.",
    "वयस्कों के लिए प्रवेश शुल्क 60 रुपये है।": "Entry costs 60 rupees for adults.",
    "முடிவுகள்": "Results", "அளவீடு": "Metric", "மதிப்பு": "Value", "துல்லியம்": "Accuracy",
}

DATASET = [
    {"id": "clean", "source": SRC, "lang": "hi", "translation": HI},
    {"id": "omitted", "source": SRC, "lang": "hi", "translation": "संग्रहालय सुबह 10 बजे खुलता है।"},
    {"id": "number", "source": SRC, "lang": "hi", "translation": HI.replace("50", "60")},
    {"id": "table", "source": MD, "lang": "ta", "translation": TA.replace("| துல்லியம் | 92% |", "| துல்லியம் 92% |")},
    {"id": "untranslated", "source": "Rivers flow.", "lang": "bn"},
]


class FakeTranslate:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        body = json.loads(request.content)
        text, target = body["messages"][0]["content"], body["target_language_code"]
        if target == "en":
            for indic, english in BACK.items():
                text = text.replace(indic, english)
        else:
            text = FORWARD[(text, target)]
        r = copy.deepcopy(TRANSLATE_OK[1])
        r["choices"][0]["message"]["content"] = text
        return httpx2.Response(200, json=r)


@pytest.fixture
def run(tmp_path, monkeypatch):
    fake = FakeTranslate()
    monkeypatch.chdir(tmp_path)  # cache and any .env lookup stay inside the test directory
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "secret-t")
    for var in ("BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "Clients", lambda cfg, root: Clients(
        cfg, root, http_client=httpx2.Client(transport=httpx2.MockTransport(fake))))
    data = tmp_path / "data.jsonl"
    data.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in DATASET), encoding="utf-8")

    def go(out):
        assert cli.main(["--dataset", str(data), "--langs", "hi,ta,bn", "--out", str(tmp_path / out), "--skip-speech"]) == 0
        return [json.loads(line) for line in (tmp_path / out / "items.jsonl").read_text(encoding="utf-8").splitlines()]

    return go, fake, tmp_path


def test_end_to_end_flags_the_right_items_and_reruns_identically(run):
    go, fake, tmp_path = run
    records = {r["id"]: r for r in go("r1")}

    assert records["clean"]["candidates"] == [] and records["clean"]["verdict"] == "clean"

    # The dropped sentence held the "50", so the number check reports it too; fusion folds both into one flag.
    omitted = {c["category"]: c for c in records["omitted"]["candidates"]}
    assert omitted["omission"]["text"] == "Entry costs 50 rupees for adults."
    assert omitted["number"]["evidence"]["kind"] == "missing" and omitted["number"]["text"] == "50"
    (flag,) = records["omitted"]["flags"]
    assert flag["category"] == "omission" and "number" in flag["alt_categories"] and flag["decision"] == "flag"

    number = [c for c in records["number"]["candidates"] if c["check"] == "numbers"]
    assert number[0]["evidence"]["kind"] == "changed" and number[0]["text"] == "60" and records["number"]["verdict"] == "flagged"

    (table,) = records["table"]["candidates"]
    assert table["evidence"]["kind"] == "table_row" and table["text"] == "| துல்லியம் 92% |" and records["table"]["verdict"] == "flagged"

    assert records["untranslated"]["checks"] == {"translation": "skipped: no translation (missing)"}
    assert all(r["checks"].get("length", "").startswith("skipped") for r in records.values() if r["id"] != "untranslated")

    meta = json.loads((tmp_path / "r1" / "run.json").read_text(encoding="utf-8"))
    assert meta["results"]["hi"]["flagged"] == 2 and meta["results"]["ta"]["flagged"] == 1
    assert "secret" not in (tmp_path / "r1" / "items.jsonl").read_text(encoding="utf-8")

    calls = fake.calls
    go("r2")
    assert fake.calls == calls  # everything from cache
    for name in ("items.jsonl", "summary.md", "summary.json"):  # determinism: a cached rerun gives the same report
        assert (tmp_path / "r1" / name).read_bytes() == (tmp_path / "r2" / name).read_bytes(), name
    assert json.loads((tmp_path / "r2" / "run.json").read_text())["cost"]["spent_inr"] == 0
