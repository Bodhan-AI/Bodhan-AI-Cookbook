"""Voice comparison: one recording per voice, scored by the same speech checks, written side by side."""

import json

from conftest import item, make_clients

from bodhan_eval import voices


def test_each_voice_is_spoken_and_compared(tmp_path, fake_bodhan, fake_llm):
    ds = tmp_path / "d.jsonl"
    ds.write_text("\n".join(json.dumps(item(f"Sentence {i}.", f"वाक्य {i}।", id=f"s{i}")) for i in range(3)) + "\n", encoding="utf-8")
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    assert voices.main(["--dataset", str(ds), "--langs", "hi", "--out", str(tmp_path / "v")], bodhan=bodhan) == 0
    data = json.loads((tmp_path / "v" / "voices.json").read_text(encoding="utf-8"))
    assert set(data["per_language"]["hi"]) == {"Kavya", "Amit"}  # the language's two voices
    assert {json.loads(r.content)["voice"] for r in fake_bodhan.requests if r.url.path == "/v1/audio/speech"} == {"Kavya", "Amit"}
    assert all(s["items"] == 3 for s in data["per_language"]["hi"].values())
    assert "| hi | Amit |" in (tmp_path / "v" / "voices.md").read_text(encoding="utf-8")
