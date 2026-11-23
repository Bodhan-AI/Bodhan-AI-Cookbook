"""LLM layer: question -> graph retrieval -> LLM picks chart tool(s) -> we build the charts.

    from llm import make_charts
    results = make_charts("Compare the two armies at Kurukshetra")
    # each result: {"tool", "args", "figure", "error", "retrieval"}  (or {"text": ...} for the written answer)
"""
import inspect
import json
import os
import time

from dotenv import load_dotenv
from groq import RateLimitError

from charts import REGISTRY  # {"bar": function, "line": function, ...}
from graph_rag import VectorlessGraphRAG, load_graph
from keypool import new_client  # a fresh Groq client per call, rotating through our API keys

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

# ---------------------------------------------------------------- 1. Setup
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GRAPH_FILES = {"mahabharata": "mahabharata_graph.json", "helios": "helios_group.json"}
GRAPH_NAME = os.getenv("KNOWLEDGE_GRAPH", "mahabharata")  # which knowledge graph answers questions
GRAPH = load_graph(os.path.join(HERE, GRAPH_FILES[GRAPH_NAME]))
RAG = VectorlessGraphRAG(max_context_chars=12000)  # context is capped so the chart call stays small

# ---------------------------------------------------------------- 2. Describe the chart tools to the LLM
_NUMBERS = {"type": "array", "items": {"type": "number"}}
_STRINGS = {"type": "array", "items": {"type": "string"}}
_ANYTHING = {"type": "array", "items": {}}
_NUMBERS_OR_DICT = {"anyOf": [_NUMBERS, {"type": "object", "additionalProperties": _NUMBERS}]}

# JSON type of each chart argument (anything not listed is a string)
PARAM_TYPES = {
    "x": _ANYTHING, "y": _NUMBERS_OR_DICT, "values": _NUMBERS_OR_DICT, "series": _NUMBERS_OR_DICT,
    "labels": _STRINGS, "categories": _STRINGS, "stages": _STRINGS, "tasks": _STRINGS, "groups": _STRINGS,
    "parents": _STRINGS, "sources": _STRINGS, "targets": _STRINGS, "dates": _STRINGS,
    "lower": _NUMBERS, "upper": _NUMBERS, "size": _NUMBERS, "open": _NUMBERS, "high": _NUMBERS,
    "low": _NUMBERS, "close": _NUMBERS, "volume": _NUMBERS,
    "color": _ANYTHING, "starts": _ANYTHING, "ends": _ANYTHING,
    "z": {"type": "array", "items": _NUMBERS}, "data": {"type": "object", "additionalProperties": _NUMBERS},
    "value": {"type": "number"}, "min_value": {"type": "number"}, "max_value": {"type": "number"},
    "target": {"type": "number"}, "bins": {"type": "integer"},
    "smooth": {"type": "boolean"}, "markers": {"type": "boolean"}, "stacked": {"type": "boolean"},
    "horizontal": {"type": "boolean"}, "show_values": {"type": "boolean"}, "trendline": {"type": "boolean"},
    "show_total": {"type": "boolean"}, "show_points": {"type": "boolean"}, "fill": {"type": "boolean"},
    "mode": {"type": "string", "enum": ["group", "stack", "relative"]},
}
HIDDEN = {"show", "save", "width", "height", "theme_dark", "kw", "donut"}  # set by the code, not the LLM


def make_tool_spec(name, fn):
    """Describe ONE chart function to the LLM: name, description, arguments."""
    properties, required = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.name in HIDDEN or p.kind is p.VAR_KEYWORD:
            continue
        properties[p.name] = PARAM_TYPES.get(p.name, {"type": "string"})
        if name == "heatmap" and p.name in ("x", "y"):  # heatmap x/y are text labels, not numbers
            properties[p.name] = _ANYTHING
        if p.default is inspect.Parameter.empty:  # no default -> mandatory
            required.append(p.name)
    return {"type": "function", "function": {
        "name": name, "description": (inspect.getdoc(fn) or "").split("\n")[0],
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [make_tool_spec(name, fn) for name, fn in REGISTRY.items()]

SYSTEM_PROMPT = """You turn a user's request into chart tool calls. Pick the best chart tool(s) and fill in arguments.
- If the request contains data, use exactly that. Otherwise use your own knowledge to supply realistic data,
  and add "(approx.)" to the title. Never ask the user for data; always make the tool call(s).
- Lists passed to one tool must have equal lengths. Give each chart a short descriptive `title`.
- Make exactly one tool call per chart requested."""

CONTEXT_PROMPT = """Context retrieved from the knowledge graph is below. Answer from it, THROUGH the chart tools:
turn its facts into data (a sunburst/treemap for lineages or shares, a sankey for flows or alliances, a bar/line
for numbers over time, ...). Use names and numbers EXACTLY as in the context; never invent figures.
Where the sources differ, say so. Also write a short plain-text answer in your reply.

RETRIEVED CONTEXT:
"""


# ---------------------------------------------------------------- 3. Ask the LLM, build the charts
def ask_llm(query, context=""):
    """Returns ([(tool_name, args), ...], written_text_or_None)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    if context:
        messages.insert(1, {"role": "system", "content": CONTEXT_PROMPT + context})
    for attempt in range(5):  # retry when Groq says "rate limit"
        try:
            reply = new_client().chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto", temperature=0, top_p = 0.2
            ).choices[0].message
            break
        except RateLimitError:
            if attempt == 4:
                raise
            time.sleep(2 + attempt * 3)
    calls = [(tc.function.name, json.loads(tc.function.arguments or "{}")) for tc in reply.tool_calls or []]
    return calls, reply.content


def build_figure(tool, args):
    """Call the real chart function from charts/ with the LLM's arguments."""
    clean = {k: v for k, v in args.items() if v is not None and k not in HIDDEN}
    return REGISTRY[tool](show=False, **clean)


def make_charts(query):
    """MAIN FUNCTION (used by server.py): question -> list of results (written answer first, then one per chart)."""
    retrieval = RAG.retrieve(query, GRAPH)                       # 1) walk the knowledge graph
    calls, text = ask_llm(query, retrieval["context_text"])      # 2) LLM answers through the chart tools
    if not text and retrieval["context"]:                        # 3) charts-only reply -> write the answer too
        try:
            text = RAG._generate_answer(query, retrieval["context"])
        except RateLimitError:
            pass                                                 # keep the charts even if this extra call is limited

    results = []
    for tool, args in calls:
        try:
            results.append({"tool": tool, "args": args, "figure": build_figure(tool, args), "error": None})
        except Exception as e:                                   # the LLM gave bad arguments for this chart
            results.append({"tool": tool, "args": args, "figure": None, "error": f"{type(e).__name__}: {e}"})
    if text or not results:
        results.insert(0, {"tool": None, "args": {}, "figure": None, "error": None,
                           "text": text or "No chart could be chosen for that request."})
    for r in results:
        r["retrieval"] = retrieval                               # graph walk + context, shown in the UI
    return results


if __name__ == "__main__":
    import sys
    for r in make_charts(" ".join(sys.argv[1:]) or "Compare the two armies at Kurukshetra"):
        print(r["tool"], json.dumps(r["args"])[:300], r["error"] or "ok")
