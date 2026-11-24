import json
from pathlib import Path

from PIL import Image

from bodhan_shared.tools import ToolHub, canonical_numbers, extract_protected


def test_workspace_blocks_traversal(tmp_path):
    hub = ToolHub(tmp_path)
    r = hub.call("read_text", {"path": "../../etc/passwd"})
    assert not r["ok"]
    assert r["error"]["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_markdown_structure_numbers(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    src = "# Heading\n\n| Name | Quantity |\n|---|---|\n| Voltage | 220 V |\n\nSolve $x^2 = 4$."
    result = hub.call("translate_text", {"target_language": "ta", "text": src})
    assert result["ok"], result
    dest = Path(result["output_path"])
    content = dest.read_text()
    assert "|---|---|" in content
    assert "$x^2 = 4$" in content
    assert canonical_numbers(content) == canonical_numbers(src)
    assert result["qa"]["numbers_preserved"]
    assert len(fake_gateway) > 0


def test_ocr_image_and_source_file(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    Image.new("RGB", (30, 30), "white").save(tmp_path / "in.png")
    result = hub.call("ocr_document", {"input_path": "in.png"})
    assert result["ok"], result
    assert result["pages_ok"] == 1
    assert "triangle" in Path(result["output_path"]).read_text()
    assert result["pages_failed"] == 0


def test_speak_long_audio_and_verify(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    source = "Hello! " * 80
    Path(tmp_path / "text.txt").write_text(source)
    result = hub.call("speak_text", {"language": "en", "input_path": "text.txt",
                                      "output_path": "artifacts/audio.wav"})
    assert result["ok"], result
    assert result["parts"] >= 2
    assert Path(result["output_path"]).is_file()
    assert result["verified"]
    assert result["ai_generated"]


def test_subtitle_generation(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    (tmp_path / "segments.json").write_text(json.dumps({"segments": [
        {"start": 0, "end": 2, "text": "Hello"}]}))
    r = hub.call("create_subtitles", {"segments_path": "segments.json"})
    assert r["ok"]
    assert "Hello" in Path(r["output_path"]).read_text()


def test_indic_text_policy_guards(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    result = hub.call("save_english_text", {"path": "artifacts/forbidden.md", "text": "नमस्ते"})
    assert not result["ok"] and result["error"]["code"] == "MODEL_POLICY"
    result = hub.call("speak_text", {"language": "hi", "text": "नमस्ते"})
    assert not result["ok"] and result["error"]["code"] == "MODEL_POLICY"


def test_writes_restricted_to_artifacts_and_no_credentials_as_speech(fake_gateway, tmp_path):
    hub = ToolHub(tmp_path)
    (tmp_path / ".env").write_text("BODHAN_" + "API_KEY=some-private-secret")
    assert hub.call("save_english_text", {"path": "README.md", "text": "overwrite"})["error"]["code"] == "UNSAFE_OUTPUT_PATH"
    assert hub.call("speak_text", {"language": "en", "input_path": ".env"})["error"]["code"] == "UNSAFE_TEXT_SOURCE"
    assert hub.call("ocr_document", {"input_path": ".env", "output_path": "README.md"})["error"]["code"] == "UNSAFE_OUTPUT_PATH"
