import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)


class BodhanBaseClient:
    """Base client for Bodhan AI services with bounded retry policies and latency tracking."""

    def __init__(self, api_key: str, base_url: str = "https://api.bodhan.ai", timeout: int = 30):
        self.api_key = api_key
        cleaned = base_url.strip().rstrip("/")
        if cleaned.endswith("/v1"):
            cleaned = cleaned[:-3]
        self.base_url = cleaned or "https://api.bodhan.ai"
        self.timeout = timeout

        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get_headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req_headers = headers or (self._get_headers(None if files else "application/json"))

        start_time = time.time()
        try:
            response = self.session.post(
                url,
                json=json_data,
                files=files,
                data=data,
                headers=req_headers,
                timeout=self.timeout
            )
            latency = time.time() - start_time
            logger.debug("POST %s status=%d latency=%.2fs", url, response.status_code, latency)
            return response
        except Exception as e:
            latency = time.time() - start_time
            logger.error("POST %s failed after %.2fs: %s", url, latency, e)
            raise
