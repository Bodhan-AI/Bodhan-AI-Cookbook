"""LLM client for Bodhan Task Agent.

Wraps OpenAI-compatible chat completions with tool calling, retries with backoff on 429/5xx,
token accounting, and support for models returning reasoning fields.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set

import httpx
from pydantic import BaseModel, Field


class ToolCallItem(BaseModel):
    id: str = "call_0"
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str = ""
    reasoning: Optional[str] = None
    tool_calls: List[ToolCallItem] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: Optional[str] = None
    rate_limit_wait_ms: float = 0.0


def extract_canonical_tool_name(raw_name: str, valid_tool_names: Set[str]) -> Optional[str]:
    """Extracts a declared tool name from mangled model outputs (e.g. 'indicatertype=finish', 'finish<|channel|>commentary')."""
    if not raw_name:
        return None
    # 1. Strip special tokens (<|... and ...|>)
    name = re.sub(r"<\|.*", "", raw_name).strip()
    if name in valid_tool_names:
        return name
    # 2. Check common delimiters ('=', ':', '.')
    for delim in ("=", ":", "."):
        if delim in name:
            part = name.split(delim)[-1].strip()
            if part in valid_tool_names:
                return part
    # 3. Check substring / word boundaries for valid tool names
    for vt in sorted(valid_tool_names, key=len, reverse=True):
        if re.search(
            r"(?:^|[._:=/\-\s])" + re.escape(vt) + r"(?:$|[._:=/\-\s])", name
        ) or name.endswith(vt):
            return vt
    return None


def validate_chat_messages(messages: List[Dict[str, Any]]) -> None:
    """Validates message order and structure matching OpenAI / Groq tool calling API rules.

    Rules:
    1. Every role 'tool' message must directly follow an assistant message (or other tool messages
       originating from the same assistant turn).
    2. The preceding assistant message must have a non-empty 'tool_calls' list.
    3. Each tool_call item in the assistant message must have a non-empty 'id' and 'function.name'.
    4. The 'tool' message's 'tool_call_id' must match one of the declared 'id's in that preceding assistant message.
    """
    last_assistant_tool_ids: set[str] = set()
    in_tool_block = False

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "tool":
            if not in_tool_block or not last_assistant_tool_ids:
                raise ValueError(
                    f"Message order error at index {idx}: role 'tool' message must directly follow an "
                    f"assistant message with matching tool_calls. (Found role: '{role}')"
                )
            tc_id = msg.get("tool_call_id")
            if not tc_id or tc_id not in last_assistant_tool_ids:
                raise ValueError(
                    f"Message order error at index {idx}: tool_call_id '{tc_id}' does not match any id declared "
                    f"in the preceding assistant tool_calls: {sorted(list(last_assistant_tool_ids))}"
                )
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                in_tool_block = True
                last_assistant_tool_ids = set()
                for tc in tool_calls:
                    tc_dict = (
                        tc
                        if isinstance(tc, dict)
                        else (tc.model_dump() if hasattr(tc, "model_dump") else vars(tc))
                    )
                    tc_id = tc_dict.get("id")
                    fn = tc_dict.get("function", {})
                    fn_name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
                    if not tc_id:
                        raise ValueError(f"Assistant tool_call at index {idx} is missing 'id'.")
                    if not fn_name:
                        raise ValueError(
                            f"Assistant tool_call '{tc_id}' at index {idx} is missing function name (Tools should have a name!)."
                        )
                    last_assistant_tool_ids.add(tc_id)
            else:
                in_tool_block = False
                last_assistant_tool_ids = set()
        else:
            in_tool_block = False
            last_assistant_tool_ids = set()


class BaseLLMClient:
    """Base interface for LLM client."""

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        raise NotImplementedError


class OpenAILLMClient(BaseLLMClient):
    """OpenAI-compatible LLM client calling POST /chat/completions."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 90.0,
        max_retries: int = 3,
        max_tokens: int = 2048,
    ):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip(
            "/"
        )
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", str(max_tokens)))
        self.calls_count: int = 0
        self.total_tokens_used: int = 0
        self.prompt_tokens_used: int = 0
        self.completion_tokens_used: int = 0
        self.rate_limit_429_count: int = 0
        self.rate_limit_wait_ms: float = 0.0

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        validate_chat_messages(messages)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        turn_wait_ms = 0.0

        client_timeout = httpx.Timeout(connect=15.0, read=self.timeout, write=30.0, pool=15.0)
        for attempt in range(self.max_retries + 1):
            try:
                self.calls_count += 1
                async with httpx.AsyncClient(timeout=client_timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code == 429:
                    self.rate_limit_429_count += 1
                    retry_after = resp.headers.get("retry-after")
                    sleep_sec = min(30.0, 2.0**attempt)
                    if retry_after:
                        try:
                            sleep_sec = min(30.0, max(0.01, float(retry_after)))
                        except ValueError:
                            pass
                    if attempt < self.max_retries:
                        print(
                            f"  [rate_limit] waiting {sleep_sec:.1f}s for LLM rate limit (HTTP 429)",
                            flush=True,
                        )
                        t_sleep_start = time.perf_counter()
                        try:
                            await asyncio.sleep(sleep_sec)
                        finally:
                            actual_wait_ms = round(
                                (time.perf_counter() - t_sleep_start) * 1000.0, 2
                            )
                            self.rate_limit_wait_ms += actual_wait_ms
                            turn_wait_ms += actual_wait_ms
                        continue
                    return LLMResponse(
                        error=f"LLM API rate limit error (HTTP 429): {resp.text}",
                        rate_limit_wait_ms=turn_wait_ms,
                    )

                if resp.status_code >= 500:
                    if attempt < self.max_retries:
                        await asyncio.sleep(2.0**attempt)
                        continue
                    return LLMResponse(
                        error=f"LLM API error (HTTP {resp.status_code}): {resp.text}",
                        rate_limit_wait_ms=turn_wait_ms,
                    )

                if resp.status_code == 400:
                    # Check for tool_use_failed where model leaked channel tokens like <|channel|>commentary
                    try:
                        err_data = resp.json()
                        err_obj = err_data.get("error", {})
                        if (
                            err_obj.get("code") == "tool_use_failed"
                            and "failed_generation" in err_obj
                        ):
                            failed_gen = err_obj["failed_generation"]
                            gen_obj = None
                            if isinstance(failed_gen, str):
                                try:
                                    gen_obj = json.loads(failed_gen)
                                except Exception:
                                    m = re.search(r"\{.*\}", failed_gen, re.DOTALL)
                                    if m:
                                        gen_obj = json.loads(m.group(0))
                            elif isinstance(failed_gen, dict):
                                gen_obj = failed_gen

                            if isinstance(gen_obj, dict):
                                raw_name = gen_obj.get("name", "")
                                valid_tool_names = {
                                    t.get("function", {}).get("name") for t in (tools or [])
                                }
                                recovered_name = extract_canonical_tool_name(
                                    raw_name, valid_tool_names
                                )
                                if recovered_name:
                                    raw_args = gen_obj.get("arguments", {})
                                    if isinstance(raw_args, str):
                                        try:
                                            raw_args = json.loads(raw_args)
                                        except Exception:
                                            raw_args = {}
                                    recovered = ToolCallItem(
                                        id=f"call_recovered_{self.calls_count}",
                                        name=recovered_name,
                                        arguments=raw_args if isinstance(raw_args, dict) else {},
                                    )
                                    return LLMResponse(
                                        content="",
                                        tool_calls=[recovered],
                                        prompt_tokens=0,
                                        completion_tokens=0,
                                        total_tokens=0,
                                        rate_limit_wait_ms=turn_wait_ms,
                                    )
                    except Exception:
                        pass

                    return LLMResponse(
                        error=f"LLM API client error (HTTP {resp.status_code}): {resp.text}",
                        rate_limit_wait_ms=turn_wait_ms,
                    )

                if resp.status_code != 200:
                    return LLMResponse(
                        error=f"LLM API client error (HTTP {resp.status_code}): {resp.text}",
                        rate_limit_wait_ms=turn_wait_ms,
                    )

                data = resp.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})

                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or msg.get("thinking") or None

                valid_tool_names = {t.get("function", {}).get("name") for t in (tools or [])}
                tool_calls: List[ToolCallItem] = []
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    cleaned_name = extract_canonical_tool_name(fn_name, valid_tool_names) or fn_name
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}
                    tool_calls.append(
                        ToolCallItem(
                            id=tc.get("id", "call_0"), name=cleaned_name or fn_name, arguments=args
                        )
                    )

                usage = data.get("usage", {})
                p_toks = usage.get("prompt_tokens", 0)
                c_toks = usage.get("completion_tokens", 0)
                tot_toks = usage.get("total_tokens", 0)
                self.prompt_tokens_used += p_toks
                self.completion_tokens_used += c_toks
                self.total_tokens_used += tot_toks

                return LLMResponse(
                    content=content,
                    reasoning=reasoning,
                    tool_calls=tool_calls,
                    prompt_tokens=p_toks,
                    completion_tokens=c_toks,
                    total_tokens=tot_toks,
                    rate_limit_wait_ms=turn_wait_ms,
                )

            except httpx.TimeoutException:
                if attempt < self.max_retries:
                    await asyncio.sleep(2.0**attempt)
                    continue
                return LLMResponse(error="LLM request timed out.", rate_limit_wait_ms=turn_wait_ms)
            except Exception as e:
                return LLMResponse(
                    error=f"LLM network failure: {str(e)}", rate_limit_wait_ms=turn_wait_ms
                )

        return LLMResponse(error="Failed after maximum retries.", rate_limit_wait_ms=turn_wait_ms)


class ScriptedFakeLLMClient(BaseLLMClient):
    """Scripted fake LLM client for tests without making real API calls."""

    def __init__(self, script: List[LLMResponse]):
        self.script = list(script)
        self.calls_recorded = []
        self.calls_count = 0
        self.total_tokens_used = 0
        self.prompt_tokens_used = 0
        self.completion_tokens_used = 0
        self.rate_limit_wait_ms = 0.0

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        validate_chat_messages(messages)
        self.calls_recorded.append({"messages": messages, "tools": tools})
        self.calls_count += 1

        # If tools is None (the planning turn):
        if tools is None:
            if self.script and not self.script[0].tool_calls:
                resp = self.script.pop(0)
            else:
                resp = LLMResponse(
                    content="Plan:\n1. Read input file with read_text_file.\n2. Translate text to Hindi with indic_translate.\n3. Synthesize speech with indic_speak.\n4. Verify output.",
                    prompt_tokens=50,
                    completion_tokens=40,
                    total_tokens=90,
                )
            self.total_tokens_used += resp.total_tokens
            self.prompt_tokens_used += resp.prompt_tokens
            self.completion_tokens_used += resp.completion_tokens
            return resp

        if self.script:
            resp = self.script.pop(0)
            self.total_tokens_used += resp.total_tokens
            self.prompt_tokens_used += resp.prompt_tokens
            self.completion_tokens_used += resp.completion_tokens
            return resp
        # Default stop
        return LLMResponse(content="Task finished.")
