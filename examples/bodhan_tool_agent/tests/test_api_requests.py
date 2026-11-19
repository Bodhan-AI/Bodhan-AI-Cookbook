from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
import unittest
from unittest.mock import patch

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.api_requests import MAX_CONCURRENT_REQUESTS, post_bodhan
from bodhan_mcp.request_budget import BudgetExhausted, RequestBudget, active_budget

PRICE = Decimal("0.20")
URL = "https://mock.test/ocr"


class BodhanRequestsTests(unittest.TestCase):
    def setUp(self):
        self.budget = RequestBudget(PRICE)
        token = active_budget.set(self.budget)
        self.addCleanup(active_budget.reset, token)

    def test_retry_after_rejection_and_connect_timeout_at_exact_budget(self):
        def rejected(request):
            return httpx.Response(429, headers={"Retry-After": "1"})

        def disconnected(request):
            raise httpx.ConnectTimeout("Offline", request=request)

        def success(request):
            return httpx.Response(200)

        outcomes = iter((rejected, disconnected, success))
        with (
            httpx.Client(transport=httpx.MockTransport(lambda request: next(outcomes)(request))) as client,
            patch("bodhan_mcp.api_requests.sleep") as wait,
        ):
            response, reservation = post_bodhan(client, URL, "indic-ocr", PRICE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reservation, 2)
        self.assertEqual(self.budget.total, PRICE)
        self.assertEqual([entry["cost_inr"] for entry in self.budget.requests], [0, 0, PRICE])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [1, 0.5])

    def test_long_retry_after_returns_without_sleep_or_charge(self):
        with (
            httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "120"})
            )) as client,
            patch("bodhan_mcp.api_requests.sleep") as wait,
        ):
            response, reservation = post_bodhan(client, URL, "indic-ocr", PRICE)
        self.assertEqual(response.status_code, 429)
        self.assertIsNone(reservation)
        self.assertEqual(self.budget.total, 0)
        wait.assert_not_called()

    def test_ambiguous_failure_is_not_retried_or_refunded(self):
        for outcome in (httpx.ReadTimeout("No response"), httpx.Response(503)):
            with self.subTest(outcome=outcome):
                budget = RequestBudget(PRICE)
                token = active_budget.set(budget)
                try:
                    with patch.object(httpx.Client, "post") as post, httpx.Client() as client:
                        if isinstance(outcome, Exception):
                            post.side_effect = outcome
                            with self.assertRaises(httpx.ReadTimeout):
                                post_bodhan(client, URL, "indic-ocr", PRICE)
                        else:
                            post.return_value = outcome
                            response, _ = post_bodhan(client, URL, "indic-ocr", PRICE)
                            self.assertEqual(response.status_code, 503)
                        with self.assertRaisesRegex(ToolError, "duplicate_request"):
                            post_bodhan(client, URL, "indic-ocr", PRICE)
                        with self.assertRaises(BudgetExhausted):
                            post_bodhan(client, URL, "indic-ocr", PRICE, json={"input": "different"})
                        post.assert_called_once()
                    self.assertEqual(budget.total, PRICE)
                finally:
                    active_budget.reset(token)

    def test_cache_keys_cover_audio_bytes_and_language_and_skip_reservations(self):
        def run(client, audio, language):
            return post_bodhan(
                client, URL, "indic-transcribe", PRICE,
                data={"language": language}, files={"file": ("audio.wav", audio, "audio/wav")},
            )

        with patch.object(httpx.Client, "post", return_value=httpx.Response(200)) as post, httpx.Client() as client:
            response, reservation = run(client, b"audio", "hi")
            self.assertEqual(reservation, 0)
            self.assertEqual(run(client, b"audio", "hi"), (response, None))
            for audio, language in ((b"different", "hi"), (b"audio", "bn")):
                with self.assertRaises(BudgetExhausted):
                    run(client, audio, language)
            post.assert_called_once()
            self.assertEqual(self.budget.total, PRICE)
            self.assertEqual(len(self.budget.requests), 1)

            token = active_budget.set(RequestBudget(PRICE))
            try:
                self.assertEqual(run(client, b"audio", "hi")[1], 0)
                self.assertEqual(post.call_count, 2)
            finally:
                active_budget.reset(token)

    def test_concurrency_limit_applies_across_models(self):
        barrier = Barrier(MAX_CONCURRENT_REQUESTS + 1)
        release_slots = Event()

        def blocking(request):
            barrier.wait(timeout=5)
            if not release_slots.wait(timeout=5):
                raise RuntimeError("Test request was not released")
            return httpx.Response(200)

        def run(model):
            with httpx.Client(transport=httpx.MockTransport(blocking)) as client:
                return post_bodhan(client, URL, model, PRICE)[0].status_code

        models = ["indic-ocr", "indic-speak", "indic-translate", "indic-transcribe"]
        with ThreadPoolExecutor() as pool:
            pending = [pool.submit(run, model) for model in models[:MAX_CONCURRENT_REQUESTS]]
            try:
                barrier.wait(timeout=5)
                with (
                    patch("bodhan_mcp.api_requests.SLOT_TIMEOUT_SECONDS", 0),
                    patch.object(httpx.Client, "post") as post,
                    httpx.Client() as client,
                ):
                    with self.assertRaisesRegex(ToolError, "concurrency_limit"):
                        post_bodhan(client, URL, "indic-ocr", PRICE)
                    self.assertEqual(self.budget.total, 0)
                    post.assert_not_called()
            finally:
                release_slots.set()
            self.assertEqual([request.result() for request in pending], [200] * MAX_CONCURRENT_REQUESTS)
