#!/usr/bin/env python3
"""Diagnostic script to verify LLM endpoint connectivity, tool calling, and rate-limit headers.

Loads .env via python-dotenv and sends a single small chat-completions request with
a dummy 'echo' tool. Never prints API keys or Authorization headers.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv


def print_rate_limit_headers(headers: httpx.Headers) -> None:
    """Prints x-ratelimit-* and retry-after response headers (never secrets)."""
    rl_headers = sorted(
        (k, v)
        for k, v in headers.items()
        if k.lower().startswith("x-ratelimit-") or k.lower() == "retry-after"
    )
    print("Rate-Limit Headers:")
    if not rl_headers:
        print("  (none returned)")
        return
    for k, v in rl_headers:
        print(f"  {k}: {v}")


def main() -> int:
    load_dotenv(override=True)

    base_url = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    model = (os.getenv("LLM_MODEL") or "").strip()
    api_key = (os.getenv("LLM_API_KEY") or "").strip()

    if not base_url or not model or not api_key:
        missing = [
            name
            for name, val in [
                ("LLM_BASE_URL", base_url),
                ("LLM_MODEL", model),
                ("LLM_API_KEY", api_key),
            ]
            if not val
        ]
        print(
            f"ERROR: Missing required environment variable(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [
            {"role": "user", "content": "Call the echo tool with text hello"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echoes back the provided text string.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "The text to echo.",
                            }
                        },
                        "required": ["text"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
    }

    print(f"Model: {model}")
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        print(f"HTTP Status: TIMEOUT ({exc})")
        return 1
    except httpx.HTTPError as exc:
        print(f"HTTP Status: NETWORK_ERROR ({exc})")
        return 1

    print(f"HTTP Status: {resp.status_code}")
    print_rate_limit_headers(resp.headers)

    if resp.status_code != 200:
        err_msg = resp.text
        try:
            body = resp.json()
            if isinstance(body, dict) and "error" in body:
                err_obj = body["error"]
                if isinstance(err_obj, dict):
                    err_msg = err_obj.get("message") or json.dumps(err_obj, ensure_ascii=False)
                else:
                    err_msg = str(err_obj)
        except Exception:
            pass
        print(f"Error: {err_msg}")
        return 1

    try:
        data = resp.json()
    except Exception as exc:
        print(f"Error: Failed to parse JSON response body: {exc}")
        return 1

    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    print(f"Prompt Tokens: {prompt_tokens}")
    print(f"Completion Tokens: {completion_tokens}")

    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    tool_calls = message.get("tool_calls") or []

    if tool_calls:
        print(f"Tool Call Returned: Yes ({len(tool_calls)})")
        for idx, tc in enumerate(tool_calls, 1):
            fn = tc.get("function") or {}
            fn_name = fn.get("name", "")
            fn_args = fn.get("arguments", "")
            print(f"  [{idx}] name={fn_name} arguments={fn_args}")
    else:
        print("Tool Call Returned: No")
        content = (message.get("content") or "").strip()
        if content:
            print(f"Message Content: {content[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
