"""Regression tests covering all hardening fixes discovered during real-world & adversarial QA."""

import builtins
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["BODHAN_MOCK"] = "1"

from bodhan_agent.agent import TaskAgent
from bodhan_mcp.client.cache import GLOBAL_CACHE
from bodhan_mcp.client.client import BodhanClient
from bodhan_mcp.server import MCPServer
from bodhan_mcp.tools import (
    bodhan_detect_language,
    bodhan_make_subtitles,
    bodhan_ocr,
    bodhan_speak,
    bodhan_transcribe,
    bodhan_translate,
    bodhan_verify_speech,
    bodhan_verify_translation,
)
from bodhan_mcp.tools.speak import _split_into_sentences


def _make_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x01" * int(rate * seconds))


class TestHardeningRegressions(unittest.TestCase):
    def test_translate_canonical_script_variants_ks_deva_mni_beng_sd_arab(self):
        for lang in ("ks-Deva", "ks-deva", "mni-Beng", "sd-Arab"):
            res = bodhan_translate(text="Hello world", target_language=lang)
            self.assertTrue(res.translation)

    def test_transcribe_handles_mp3_and_mp4_in_conversion_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "long.mp3"
            mp3.write_bytes(b"ID3" + b"\x00" * 2_000_000)
            client_live = BodhanClient(mock_mode=False, transcribe_key="fake")
            with patch("shutil.which", return_value=None):
                with self.assertRaises(ValueError) as ctx:
                    bodhan_transcribe(str(mp3), client=client_live)
                self.assertIn("ffmpeg", str(ctx.exception))

    def test_speak_verify_retry_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            from bodhan_mcp.client.cache import ContentCache
            client = BodhanClient(
                mock_mode=False,
                speak_key="fake",
                transcribe_key="fake",
                cache=ContentCache(cache_dir=Path(tmp) / "cache"),
            )
            speak_calls = 0
            mock_wav = client._generate_mock_wav(2.0)

            def fake_post(url, **kwargs):
                nonlocal speak_calls
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                if "speech" in url:
                    speak_calls += 1
                    resp.content = mock_wav
                else:
                    resp.json.return_value = {"text": "x", "language": "hi"}
                return resp

            with patch.object(client.session, "post", side_effect=fake_post):
                bodhan_speak(
                    text="यह एक लंबा परीक्षण वाक्य है जिसे दोहराया जाना चाहिए।",
                    language="hi",
                    output_file=str(Path(tmp) / "out.wav"),
                    verify=True,
                    client=client,
                )
            self.assertEqual(speak_calls, 2)

    def test_speak_splits_long_unpunctuated_sentence_and_validates_lang_voice(self):
        long_sent = "यह एक बहुत लंबा वाक्य है " * 30
        chunks = _split_into_sentences(long_sent)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 220 for c in chunks))

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                bodhan_speak(text="Bonjour", language="fr", output_file=str(Path(tmp) / "fr.wav"))
            with self.assertRaises(ValueError):
                bodhan_speak(text="नमस्ते", language="hi", voice="FakeVoice", output_file=str(Path(tmp) / "hi.wav"))

    def test_ocr_rejects_non_image_file_and_invalid_table_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            _make_wav(wav, 1.0)
            with self.assertRaises(ValueError):
                bodhan_ocr(str(wav))
            with self.assertRaises(ValueError):
                bodhan_ocr(str(wav), table_format="xml")

    def test_verify_speech_rejects_zero_word_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "test.wav"
            _make_wav(wav, 2.0)
            client_live = BodhanClient(mock_mode=False, transcribe_key="fake")
            with patch.object(client_live, "transcribe") as mock_tr:
                mock_tr.return_value = MagicMock(text="unrelated random words spoken here", cost_inr=0.01)
                res = bodhan_verify_speech(str(wav), expected_text="hello world testing speech synthesis", client=client_live)
            self.assertFalse(res["passed"])
            self.assertTrue(any("overlap" in i.lower() for i in res["issues"]))

    def test_verify_translation_english_target_script_and_indic_source_digits(self):
        # Untranslated Hindi for target='en' must fail
        vt_en = bodhan_verify_translation("यह हिंदी है", "यह हिंदी है", target_language="en")
        self.assertFalse(vt_en["passed"])

        # English text with a single Hindi danda must fail target='hi'
        vt_hi = bodhan_verify_translation("Hello world this is a test.", "Hello world this is still English।", target_language="hi")
        self.assertFalse(vt_hi["passed"])

        # Tamil digit '௩' in source matched by ASCII '3' in target must pass
        vt_num = bodhan_verify_translation("காலம் - ௩.", "Time - 3.", target_language="en")
        self.assertTrue(vt_num["passed"], f"Unexpected issues: {vt_num['issues']}")

    def test_detect_language_arabic_and_chinese_not_supported(self):
        det_ar = bodhan_detect_language(text="مرحبا بالعالم هذه لغة عربية")
        self.assertEqual(det_ar.code, "ar")
        self.assertFalse(det_ar.is_supported)

        det_zh = bodhan_detect_language(text="你好世界这是一个中文测试")
        self.assertFalse(det_zh.is_supported)

    def test_mcp_server_prompts_get_null_params_and_notifications(self):
        srv = MCPServer()
        resp_pget = srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "summarize_audio"}})
        self.assertIn("result", resp_pget)
        self.assertIn("messages", resp_pget["result"])

        resp_null_params = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": None})
        self.assertTrue(resp_null_params["result"]["isError"])

        resp_null_args = srv.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bodhan_list_voices", "arguments": None}})
        self.assertFalse(resp_null_args["result"]["isError"])

        # Missing required param returns INVALID_ARGUMENTS
        resp_missing = srv.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bodhan_translate", "arguments": {"text": "hi"}}})
        self.assertTrue(resp_missing["result"]["isError"])
        self.assertIn("INVALID_ARGUMENTS", resp_missing["result"]["content"][0]["text"])

        # Notification without 'id' must return None
        self.assertIsNone(srv.handle_request({"jsonrpc": "2.0", "method": "notifications/cancelled"}))

    def test_agent_false_positive_impossible_task_and_pre_inspection_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            txt = tmp_p / "article.txt"
            txt.write_text("French architecture and cloned sheep.", encoding="utf-8")

            a1 = TaskAgent(task="Translate this article about French architecture into Marathi", files_path=str(txt), out_dir=str(tmp_p / "out1"))
            self.assertIsNone(a1._check_impossible_task())

            a2 = TaskAgent(task="Summarize this text about cloned sheep in Tamil", files_path=str(txt), out_dir=str(tmp_p / "out2"))
            self.assertIsNone(a2._check_impossible_task())

            # Pre-inspection exceeding max_steps must return budget_exceeded cleanly and write report.json
            d = tmp_p / "many"
            d.mkdir()
            for i in range(5):
                (d / f"f{i}.txt").write_text("hi", encoding="utf-8")
            a3 = TaskAgent(task="Translate these to Hindi", files_path=str(d), out_dir=str(tmp_p / "out3"), max_steps=2)
            r3 = a3.run()
            self.assertEqual(r3["status"], "budget_exceeded")
            self.assertTrue((tmp_p / "out3" / "report.json").exists())

    def test_agent_prevents_cost_overspend_and_blocks_subtitle_escape_and_formats_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            from PIL import Image
            img = tmp_p / "ws.png"
            Image.new("RGB", (100, 100), "white").save(img)

            # Upfront cost check prevents spending ₹0.20 when max_cost_inr=0.05
            a_cost = TaskAgent(task="Translate this worksheet into Marathi and keep the table", files_path=str(img), out_dir=str(tmp_p / "out_cost"), max_cost_inr=0.05)
            r_cost = a_cost.run()
            self.assertEqual(r_cost["status"], "budget_exceeded")
            rep = json.loads((tmp_p / "out_cost" / "report.json").read_text(encoding="utf-8"))
            self.assertLessEqual(rep["total_cost_inr"], 0.05)

            # Path traversal + Ground Rule 1 blocked on bodhan_make_subtitles
            escaped = tmp_p / "escaped.srt"
            a_sec = TaskAgent(task="Test", files_path=str(img), out_dir=str(tmp_p / "out_sec"))
            with self.assertRaises(ValueError):
                a_sec.execute_tool("bodhan_make_subtitles", {"text": "नकली हिंदी", "output_file": str(escaped)}, "test")
            self.assertFalse(escaped.exists())

            # SRT timestamps >= 60s formatted as 00:01:05,000 (not 00:00:65,000)
            wav65 = tmp_p / "long65.wav"
            _make_wav(wav65, 65.0)
            a_srt = TaskAgent(task="Transcribe this Hindi voice note and give me English subtitles", files_path=str(wav65), out_dir=str(tmp_p / "out_srt"))
            a_srt.run()
            srt_content = (tmp_p / "out_srt" / "artifacts" / "subtitles_en.srt").read_text(encoding="utf-8")
            self.assertNotIn("00:00:65", srt_content)
            self.assertIn("00:01:05", srt_content)

    def test_llm_loop_max_steps_reports_budget_exceeded_and_tracer_truncates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            txt = tmp_p / "note.txt"
            txt.write_text("Hello world", encoding="utf-8")
            out_d = tmp_p / "out_llm"

            # Tracer truncates stale trace.jsonl on rerun
            a_old = TaskAgent(task="Test", files_path=str(txt), out_dir=str(out_d))
            a_old.tracer.record_step(1, "old", "tool", {}, "old", 1, 0.0)
            a_new = TaskAgent(
                task="Test max steps",
                files_path=str(txt),
                out_dir=str(out_d),
                max_steps=2,
                llm_base_url="http://localhost:9999/v1",
                llm_api_key="fake-key",
            )
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Calling list voices",
                        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bodhan_list_voices", "arguments": "{}"}}],
                    }
                }]
            }
            prev = os.environ.get("BODHAN_MOCK")
            os.environ["BODHAN_MOCK"] = "0"
            try:
                with patch("bodhan_agent.agent.requests.post", return_value=mock_resp):
                    res = a_new.run()
            finally:
                if prev is not None:
                    os.environ["BODHAN_MOCK"] = prev
            self.assertEqual(res["status"], "budget_exceeded")
            self.assertIsNotNone(GLOBAL_CACHE.cache_dir)


if __name__ == "__main__":
    unittest.main()
