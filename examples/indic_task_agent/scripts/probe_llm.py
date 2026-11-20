"""Throwaway probe: does the LLM (Groq) accept our tool schemas and make a tool call?

Run from examples/indic_task_agent:  python scripts/probe_llm.py
Costs nothing from the Bodhan credit (it only calls the LLM).
"""

import asyncio
import json
import sys
from typing import Any

import httpx

from bodhan_mcp.config import load_settings
from bodhan_mcp.server import build_server

sys.stdout.reconfigure(encoding="utf-8")


def simplify(schema: Any) -> Any:
    """Make a JSON schema friendlier to LLM providers.

    * anyOf [X, null]  ->  X   (an optional parameter is just one that is not in `required`)
    * drop 'title' and null defaults, which only add noise
    """
    if isinstance(schema, list):
        return [simplify(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    options = [o for o in s.get("anyOf", []) if o.get("type") != "null"]
    if "anyOf" in s and len(options) == 1:
        s.pop("anyOf")
        s = {**simplify(options[0]), **s}
    s.pop("title", None)
    if "default" in s and s["default"] is None:
        del s["default"]
    if "properties" in s:
        s["properties"] = {name: simplify(sub) for name, sub in s["properties"].items()}
    if "items" in s:
        s["items"] = simplify(s["items"])
    return s


cfg = load_settings()
if not (cfg.llm_base_url and cfg.llm_model and cfg.llm_api_key):
    sys.exit("Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY in .env")

tools = asyncio.run(build_server().list_tools())
openai_tools = [{"type": "function", "function": {
    "name": t.name, "description": t.description, "parameters": simplify(t.input_schema)}} for t in tools]
print("model:", cfg.llm_model, "| tools:", len(openai_tools))

body = {
    "model": cfg.llm_model,
    "temperature": 0,
    "messages": [
        {"role": "system", "content": "You are a careful assistant. Use the tools to answer."},
        {"role": "user", "content": "Can Bodhan turn text into speech in Bhojpuri? Check, do not guess."},
    ],
    "tools": openai_tools,
    "tool_choice": "auto",
}
r = httpx.post(cfg.llm_base_url.rstrip("/") + "/chat/completions",
               headers={"Authorization": f"Bearer {cfg.llm_api_key}"}, json=body, timeout=60)
print("HTTP", r.status_code)
if r.status_code != 200:
    print("ERROR BODY:", r.text[:800])
    sys.exit(1)
d = r.json()
msg = d["choices"][0]["message"]
print("finish_reason:", d["choices"][0]["finish_reason"])
print("tool_calls:", json.dumps(msg.get("tool_calls"), ensure_ascii=False, indent=1))
print("content:", msg.get("content"))
print("usage:", d.get("usage"))