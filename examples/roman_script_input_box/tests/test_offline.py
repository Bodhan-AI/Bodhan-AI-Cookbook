"""Offline tests: no key, no network.   python -m unittest discover -s tests"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))
sys.path.insert(0, str(RECIPE / "eval"))

from compare_modes import cer, normalize  # noqa: E402

import app  # noqa: E402
import bodhan_client  # noqa: E402
from bodhan_client import BodhanError, Client, Result, chat_instruction, parse_chat, parse_error, parse_retry_after  # noqa: E402
from converter import MAX_NEW_WORDS, Converter, TooManyWords, in_script, strip_punct, tokenize  # noqa: E402


class FakeClient(Client):
    """Answers from a dict and counts calls, so the converter and server run without the API."""

    def __init__(self, table: dict[str, str], fail_on: str | None = None):
        self.table, self.calls, self.route, self.fail_on = table, [], "fake", fail_on
        self._lock = threading.Lock()

    def transliterate(self, text, language, script="native"):
        with self._lock:
            self.calls.append(text)
        if text == self.fail_on:
            raise BodhanError(429, "throttling_error", "slow down", retry_after=30)
        return Result(self.table.get(text, f"<{text}>"), 1.0, "fake", False)


class FakeResponse:
    def __init__(self, status: int, body: dict | str, headers: dict | None = None):
        self.status_code, self.headers = status, headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.ok = 200 <= status < 300

    def json(self):
        return json.loads(self.text)


# ---- bodhan_client -----------------------------------------------------------------


class Instruction(unittest.TestCase):
    def test_names_the_target_script(self):
        self.assertEqual(chat_instruction("tame kem cho", "gu"), "Transliterate into Gujarati script: tame kem cho")
        self.assertEqual(chat_instruction("नमस्ते", "hi", "roman"), "Transliterate into roman script: नमस्ते")

    def test_parse_chat_reads_truncation(self):
        body = {"choices": [{"finish_reason": "length", "message": {"content": " कल \n"}}]}
        self.assertEqual(parse_chat(body), ("कल", True))

    def test_parse_chat_without_choices_is_an_api_error(self):
        with self.assertRaises(BodhanError):
            parse_chat({"error": "odd"})


class Errors(unittest.TestCase):
    def test_retry_after_seconds_date_and_junk(self):
        self.assertEqual(parse_retry_after("36"), 36.0)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
        self.assertTrue(25 <= parse_retry_after(future) <= 30)
        self.assertIsNone(parse_retry_after("soon"))
        self.assertIsNone(parse_retry_after(None))

    def test_live_throttle_body_prefers_type_over_numeric_code(self):
        resp = FakeResponse(429, {"error": {"message": "Maximum rate limit reached for the model",
                                            "type": "throttling_error", "code": "429"}}, {"Retry-After": "36"})
        err = parse_error(resp)
        self.assertEqual((err.code, err.retry_after, err.is_budget), ("throttling_error", 36.0, False))

    def test_documented_envelope_and_gateway_404(self):
        self.assertEqual(parse_error(FakeResponse(400, {"error": {"message": "x", "code": "unsupported_language"}})).code,
                         "unsupported_language")
        err = parse_error(FakeResponse(404, {"detail": "Not Found"}))
        self.assertEqual((err.code, err.message), ("http_error", "Not Found"))

    def test_budget_429_is_detected(self):
        self.assertTrue(parse_error(FakeResponse(429, {"error": {"type": "budget_exceeded", "message": "x"}})).is_budget)


class Retries(unittest.TestCase):
    def _client(self, responses: list[FakeResponse], max_wait: float = 3) -> tuple[Client, mock.Mock]:
        client = Client(api_key="test-key", route="chat", max_wait=max_wait)
        session = mock.Mock()
        session.post.side_effect = responses
        client._sessions = bodhan_client.queue.LifoQueue()
        client._sessions.put(session)
        return client, session

    def test_placeholder_key_is_rejected(self):
        with self.assertRaises(RuntimeError):
            Client(api_key="<your Bodhan API key from console.bodhan.ai>")

    def test_short_retry_after_is_retried_once(self):
        ok = FakeResponse(200, {"choices": [{"finish_reason": "stop", "message": {"content": "कल"}}]})
        client, session = self._client([FakeResponse(429, {"error": {"type": "throttling_error"}}, {"Retry-After": "0"}), ok])
        self.assertEqual(client.transliterate("kal", "hi").text, "कल")
        self.assertEqual(session.post.call_count, 2)

    def test_long_retry_after_raises_without_waiting(self):
        client, session = self._client([FakeResponse(429, {"error": {"type": "throttling_error"}}, {"Retry-After": "36"})])
        with self.assertRaises(BodhanError) as ctx:
            client.transliterate("kal", "hi")
        self.assertEqual((ctx.exception.retry_after, session.post.call_count), (36.0, 1))

    def test_plain_route_reads_documented_field(self):
        body = {"transliteration": "कल", "language": "hi", "script": "native", "truncated": False}
        client, session = self._client([FakeResponse(200, body)])
        client.route = "plain"
        self.assertEqual(client.transliterate("kal", "hi").text, "कल")
        self.assertTrue(session.post.call_args.args[0].endswith("/transliterate"))


# ---- converter ---------------------------------------------------------------------


class Tokenize(unittest.TestCase):
    def test_round_trip_keeps_punctuation_and_numbers(self):
        text = "kal 10 baje, meeting hai!  don't forget"
        self.assertEqual("".join(p for p, _ in tokenize(text)), text)

    def test_only_roman_letters_are_words(self):
        self.assertEqual([p for p, w in tokenize("kal मीटिंग hai 5") if w], ["kal", "hai"])

    def test_apostrophe_stays_inside_a_word(self):
        self.assertIn(("don't", True), tokenize("don't go"))

    def test_script_checks(self):
        self.assertTrue(in_script("मीटिंग", "hi"))
        self.assertTrue(in_script("છો", "gu"))
        self.assertFalse(in_script("ਮੀਟਿੰਗ", "hi"))     # Gurmukhi answer for a Hindi request
        self.assertFalse(in_script("meeting", "hi"))
        self.assertEqual(strip_punct("है।"), "है")


class WordMode(unittest.TestCase):
    def test_reassembles_and_caches(self):
        fake = FakeClient({"kal": "कल", "meeting": "मीटिंग", "hai": "है"})
        conv = Converter(fake)
        first = conv.word("Kal meeting hai, kal!", "hi")
        self.assertEqual(first.text, "कल मीटिंग है, कल!")
        self.assertEqual(sorted(fake.calls), ["hai", "kal", "meeting"])   # "Kal"/"kal" asked once
        second = conv.word("kal hai", "hi")
        self.assertEqual((second.calls, second.cached), (0, 2))

    def test_cache_is_per_language(self):
        fake = FakeClient({"meeting": "মিটিং"})
        conv = Converter(fake)
        conv.word("meeting", "hi")
        conv.word("meeting", "bn")
        self.assertEqual(len(fake.calls), 2)

    def test_wrong_script_answer_is_shown_but_not_cached(self):
        fake = FakeClient({"meeting": "ਮੀਟਿੰਗ"})
        conv = Converter(fake)
        self.assertEqual(conv.word("meeting", "hi").text, "ਮੀਟਿੰਗ")
        self.assertEqual(conv.convert("meeting", "hi", "draft").text, "meeting")

    def test_new_words_are_capped(self):
        fake = FakeClient({})
        words = " ".join(f"{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(MAX_NEW_WORDS + 1))
        with self.assertRaises(TooManyWords):
            Converter(fake).word(words, "hi")
        self.assertEqual(fake.calls, [])

    def test_a_failed_word_raises_and_keeps_answers_already_paid_for(self):
        fake = FakeClient({"kal": "कल", "meeting": "मीटिंग"}, fail_on="hai")
        conv = Converter(fake)
        with self.assertRaises(BodhanError):
            conv.word("kal meeting hai", "hi")
        # Words queued behind the failure may be cancelled; any word that was actually
        # called must have been cached, so its quota isn't wasted.
        for word in set(fake.calls) - {"hai"}:
            self.assertNotEqual(conv.convert(word, "hi", "draft").text, word)

    def test_draft_makes_no_calls_and_keeps_unknown_words(self):
        fake = FakeClient({"kal": "कल"})
        conv = Converter(fake)
        conv.word("kal", "hi")
        draft = conv.convert("kal meeting", "hi", "draft")
        self.assertEqual((draft.text, draft.mode, len(fake.calls)), ("कल meeting", "draft", 1))


class SentenceSeeding(unittest.TestCase):
    def test_seeds_word_cache_without_punctuation(self):
        fake = FakeClient({"kal meeting hai.": "कल मीटिंग है।"})
        conv = Converter(fake)
        conv.sentence("kal meeting hai.", "hi")
        self.assertEqual(conv.convert("meeting hai", "hi", "draft").text, "मीटिंग है")

    def test_no_seeding_when_words_do_not_line_up_or_script_is_wrong(self):
        fake = FakeClient({"tame kem cho": "तुमसे क्या है", "a b": "x"})
        conv = Converter(fake)
        conv.sentence("tame kem cho", "gu")      # Devanagari for a Gujarati request
        conv.sentence("a b", "hi")               # 2 words in, 1 out
        self.assertEqual(conv.convert("tame kem", "gu", "draft").text, "tame kem")
        self.assertEqual(conv.convert("a", "hi", "draft").text, "a")


# ---- app.py HTTP server ------------------------------------------------------------


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.Handler.converter = Converter(FakeClient({"kal": "कल", "kal meeting": "कल मीटिंग"}))
        app.Handler.log_message = lambda *a: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, body, headers=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = raw if raw is not None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}", "Content-Length": str(len(data))}
        h.update(headers or {})
        conn.putrequest("POST", "/api/transliterate", skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            conn.putheader(k, v)
        conn.endheaders(data)
        resp = conn.getresponse()
        out = (resp.status, json.loads(resp.read() or b"{}"))
        conn.close()
        return out

    def test_sentence_ok(self):
        status, body = self.post({"text": "kal meeting", "language": "hi", "mode": "sentence"})
        self.assertEqual((status, body["text"], body["calls"]), (200, "कल मीटिंग", 1))

    def test_rejects_other_hosts_and_origins(self):
        self.assertEqual(self.post({"text": "kal"}, {"Host": "evil.example:8000"})[0], 403)        # DNS rebinding
        self.assertEqual(self.post({"text": "kal"}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post({"text": "kal"}, {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_requires_json_content_type(self):
        self.assertEqual(self.post({"text": "kal"}, {"Content-Type": "text/plain"})[0], 415)

    def test_bad_content_length(self):
        self.assertEqual(self.post(None, {"Content-Length": "-1"}, raw=b"{}")[0], 413)
        self.assertEqual(self.post(None, {"Content-Length": "abc"}, raw=b"{}")[0], 411)

    def test_rejects_non_string_fields_and_bad_values(self):
        self.assertEqual(self.post({"text": None, "language": "hi"})[0], 400)
        self.assertEqual(self.post({"text": "kal", "language": "zz"})[0], 400)
        self.assertEqual(self.post([1, 2])[0], 400)
        self.assertEqual(self.post({"text": "a" * 501, "language": "hi"})[0], 400)

    def test_throttle_becomes_429_with_wait(self):
        with mock.patch.object(app.Handler.converter, "convert",
                               side_effect=BodhanError(429, "throttling_error", "x", retry_after=36)):
            status, body = self.post({"text": "kal", "language": "hi"})
        self.assertEqual(status, 429)
        self.assertIn("36 s", body["error"])

    def test_bug_is_500_not_bad_gateway(self):
        with mock.patch.object(app.Handler.converter, "convert", side_effect=KeyError("oops")), \
             self.assertLogs("roman_input_box", "ERROR"):
            self.assertEqual(self.post({"text": "kal", "language": "hi"})[0], 500)


# ---- eval scoring ------------------------------------------------------------------


class Scoring(unittest.TestCase):
    def test_normalize_drops_punctuation(self):
        self.assertEqual(normalize("कल  मीटिंग है।"), "कल मीटिंग है")

    def test_cer_uses_closest_reference(self):
        self.assertEqual(cer("मैं घर जा रहा हूं", ["मैं घर जा रहा हूँ", "मैं घर जा रहा हूं"]), 0)
        self.assertGreater(cer("तुमसे क्या है", ["તમે કેમ છો"]), 0.9)


if __name__ == "__main__":
    unittest.main()
