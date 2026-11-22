"""HTTP client for Bodhan models with exponential backoff, timeouts, and concurrency limits."""

from __future__ import annotations

import time
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading

from bodhan_mcp.cache import DiskCache
from bodhan_mcp.config import Settings
from bodhan_mcp.pricing import BudgetManager


class BodhanAPIError(Exception):
    """Raised when an API call fails after retries."""

    def __init__(self, message: str, status_code: int | None = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class BodhanClient:
    """Robust client managing concurrency, retries, timeouts, caching, and budgets."""

    def __init__(
        self,
        settings: Settings,
        cache: DiskCache | None = None,
        budget: BudgetManager | None = None,
        max_concurrency: int = 3,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ):
        self.settings = settings
        self.cache = cache or DiskCache()
        self.budget = budget or BudgetManager()
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.semaphore = threading.Semaphore(max_concurrency)

        # Session with built-in connection pooling
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def post(
        self,
        endpoint: str,
        model_id: str,
        json_data: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        timeout: float = 60.0,
    ) -> requests.Response:
        """Executes a POST request to Bodhan API with concurrency throttling and backoff."""
        key = self.settings.get_model_key(model_id)
        headers = {"Authorization": f"Bearer {key}"}
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        url = f"{self.settings.bodhan_base_url.rstrip('/')}/{endpoint.lstrip('/')}"

        with self.semaphore:
            attempt = 0
            while attempt < self.max_retries:
                attempt += 1
                try:
                    resp = self.session.post(
                        url,
                        headers=headers,
                        json=json_data,
                        files=files,
                        data=data,
                        timeout=timeout,
                    )
                    if resp.status_code == 200:
                        return resp
                    elif resp.status_code in (429, 500, 502, 503, 504):
                        sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise BodhanAPIError(
                            f"API call to {model_id} failed with HTTP {resp.status_code}: {resp.text[:200]}",
                            status_code=resp.status_code,
                            response_text=resp.text,
                        )
                except requests.exceptions.RequestException as e:
                    if attempt >= self.max_retries:
                        raise BodhanAPIError(f"Network error calling {model_id}: {str(e)}") from e
                    time.sleep(self.backoff_factor * (2 ** (attempt - 1)))

            raise BodhanAPIError(f"Exceeded {self.max_retries} retries calling {model_id}")
