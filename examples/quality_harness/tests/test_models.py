import concurrent.futures
import io
import json
import tempfile
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest.mock import patch

from bodhan_eval.models import BODHAN, Budget, Client, ModelError, join_wav, split_wav
from bodhan_eval.runner import main


def wav(seconds=1, value=b"\x01\x00"):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(value * (8000 * seconds))
    return output.getvalue()


class ModelTests(unittest.TestCase):
    def test_translation_404_falls_back_and_replays_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def send(url, headers, body):
                calls.append(url)
                if url.endswith("/translate"):
                    raise urllib.error.HTTPError(url, 404, "missing", {}, None)
                payload = json.loads(body)
                self.assertEqual(payload["source_language_code"], "ta")
                self.assertEqual(payload["target_language_code"], "en")
                self.assertEqual(payload["max_output_tokens"], 4096)
                return b'{"choices":[{"finish_reason":"stop","message":{"content":"25"}}]}'

            client = Client(tmp, 1, send=send, env={"BODHAN_TRANSLATE_KEY": "fixture"})
            self.assertEqual(client.translate("25", "en", "ta"), "25")
            self.assertEqual(calls, [BODHAN + "/translate", BODHAN + "/v1/chat/completions"])
            replay = Client(tmp, 0, env={})
            self.assertEqual(replay.translate("25", "en", "ta"), "25")
            self.assertEqual(replay.calls, 0)

    def test_cache_exact_input_and_no_credentials_in_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []

            def send(url, headers, body):
                sent.append(body)
                return b'{"translation":"25","truncated":false}'

            client = Client(tmp, 1, send=send, env={"BODHAN_TRANSLATE_KEY": "secret_for_test_only"})
            self.assertEqual(client.translate("25", "hi"), "25")
            self.assertEqual(client.translate("25", "hi"), "25")
            self.assertEqual(len(sent), 1)
            self.assertNotIn("secret_for_test_only", next(Path(tmp).glob("*.json")).read_text())
            # Replays need no key and reserve zero execution cost.
            replay = Client(tmp, 0, env={})
            self.assertEqual(replay.translate("25", "hi"), "25")
            self.assertEqual(replay.calls, 0)
            self.assertEqual(replay.budget.reserved, 0)
            with self.assertRaisesRegex(ModelError, "missing_configuration"):
                replay.translate("26", "hi")

    def test_budget_and_ambiguous_retry_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def send(url, headers, body):
                calls.append(url)
                raise urllib.error.HTTPError(url, 503, "failed", {}, None)

            client = Client(tmp, 0.09, retries=2, send=send, sleep=lambda _: None, env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with self.assertRaisesRegex(ModelError, "budget_exhausted"):
                client.translate("25", "hi")
            self.assertEqual(len(calls), 1)
            self.assertLessEqual(client.budget.reserved, 0.09)

    def test_nonretryable_and_truncated_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:

            def forbidden(url, headers, body):
                raise urllib.error.HTTPError(url, 401, "failed", {}, None)

            client = Client(tmp, 1, send=forbidden, env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with self.assertRaisesRegex(ModelError, "http_error:401"):
                client.translate("25", "hi")
            self.assertEqual(client.calls, 1)
        with tempfile.TemporaryDirectory() as tmp:

            def denied(*args):
                raise urllib.error.URLError(PermissionError(13, "denied"))

            client = Client(tmp, 1, send=denied, env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with self.assertRaisesRegex(ModelError, "network_access_denied"):
                client.translate("25", "hi")
            self.assertEqual(client.calls, 1)
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp, 1, send=lambda *a: b'{"translation":"25","truncated":true}', env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with self.assertRaisesRegex(ModelError, "translation_truncated"):
                client.translate("25", "hi")

    def test_duplicate_concurrent_requests_are_coalesced(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp, 1, send=lambda *a: b'{"translation":"25"}', env={"BODHAN_TRANSLATE_KEY": "fixture"})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _: client.translate("25", "hi"), range(4))), ["25"] * 4)
            self.assertEqual(client.calls, 1)
            self.assertEqual(client.hits, 3)

    def test_budget_is_thread_safe(self):
        budget = Budget(1)

        def reserve(_):
            try:
                budget.reserve(0.1)
                return True
            except ModelError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            successes = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(successes), 10)

    def test_wav_splitting_and_joining(self):
        chunks = split_wav(wav(51))
        self.assertEqual([seconds for _, seconds in chunks], [25, 25, 1])
        with wave.open(io.BytesIO(join_wav([data for data, _ in chunks])), "rb") as audio:
            self.assertEqual(audio.getnframes(), 51 * 8000)

    def test_asr_multipart_and_chunk_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bodies = []

            def send(url, headers, body):
                self.assertEqual(url, BODHAN + "/v1/audio/transcriptions")
                self.assertIn("multipart/form-data", headers["Content-Type"])
                self.assertIn(b'name="language"\r\n\r\nhi', body)
                bodies.append(body)
                return b'{"text":"25"}'

            client = Client(tmp, 1, send=send, env={"BODHAN_TRANSCRIBE_KEY": "fixture"})
            self.assertEqual(client.transcribe(wav(51), "hi"), "25 25 25")
            # Equal first/second chunks share cache because their exact input is equal.
            self.assertEqual(len(bodies), 2)


class ModelEndToEndTests(unittest.TestCase):
    def test_generate_judge_and_zero_network_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "input.jsonl"
            dataset.write_text(json.dumps({"id": "example", "source": "25", "lang": "hi"}) + "\n", encoding="utf-8")
            env = {name: "fixture" for name in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY")}
            env.update({"LLM_BASE_URL": "https://judge.example/v1", "LLM_MODEL": "fixture-judge"})
            calls = []

            def send(url, headers, body):
                calls.append(url)
                if url.endswith("/translate"):
                    return b'{"translation":"25","truncated":false}'
                if url.endswith("/audio/speech"):
                    payload = json.loads(body)
                    self.assertEqual(payload["model"], "indic-speak")
                    self.assertEqual(json.loads(payload["instructions"]), {"lang": "hi"})
                    return wav()
                if url.endswith("/audio/transcriptions"):
                    return b'{"text":"25"}'
                return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"flags":[]}'}}]}).encode()

            instances = []

            def factory(*args, **kwargs):
                instance = Client(*args, send=send, env=env)
                instances.append(instance)
                return instance

            arguments = [
                "--dataset",
                str(dataset),
                "--langs",
                "hi",
                "--generate",
                "--cache-dir",
                str(root / "cache"),
                "--llm-input-inr-per-million",
                "0",
                "--llm-output-inr-per-million",
                "0",
            ]
            with patch("bodhan_eval.runner.Client", side_effect=factory):
                main(arguments + ["--out", str(root / "first")])
                self.assertGreater(len(calls), 0)
                main(arguments + ["--out", str(root / "second"), "--baseline", str(root / "first")])
            self.assertEqual(instances[1].calls, 0)
            self.assertEqual(instances[1].budget.reserved, 0)
            for path in (root / "first").iterdir():
                self.assertEqual(path.read_bytes(), (root / "second" / path.name).read_bytes(), path.name)
            row = json.loads((root / "first" / "items.jsonl").read_text())
            self.assertEqual(row["checks"]["judge"]["repeat_agreement"], 1)
            self.assertTrue(Path(row["audio"]).is_file())
            self.assertTrue(row["generation"]["translation"]["request_keys"])
            self.assertTrue(row["generation"]["audio"]["request_keys"])


if __name__ == "__main__":
    unittest.main()
