import time

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


def post_with_retry(url: str, retries: int = 2, **kwargs) -> httpx.Response:
    """POST, and retry with a growing wait on rate limits, server errors and network problems."""
    delay = 2
    for attempt in range(retries + 1):
        try:
            reply = httpx.post(url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == retries:
                raise
        else:
            if reply.status_code not in RETRY_STATUS or attempt == retries:
                return reply
        time.sleep(delay)
        delay *= 2