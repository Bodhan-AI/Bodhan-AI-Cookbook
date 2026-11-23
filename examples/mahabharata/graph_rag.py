"""Vectorless Graph RAG: an LLM walks a nested-dict knowledge graph (e.g. mahabharata_graph.json).

At every level it picks the most relevant keys (by NAME, no embeddings), follows them down, and collects
the text of the leaves it reaches as context. Every step is recorded in `trace` so the UI can draw the walk.

    rag = VectorlessGraphRAG()
    r = rag.retrieve("Why did Bhishma take his vow?", load_graph("mahabharata_graph.json"))
    r["context"]       # [{"path": "Topic > Subtopic", "content": "..."}]
    r["context_text"]  # the same, formatted for a prompt
    r["trace"]         # [{"kind": "branch"|"leaf"|"limit", "path": [...], "depth": n, ...}]
"""
import json
import re
import time

from groq import RateLimitError

from keypool import new_client


def load_graph(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class VectorlessGraphRAG:

    def __init__(self, model="openai/gpt-oss-20b", max_branches=3, max_nodes=100, max_context_chars=30000):
        self.model = model
        self.max_branches = max_branches              # keys followed per level
        self.max_nodes = max_nodes                    # cost guard: nodes visited per question
        self.max_context_chars = max_context_chars
        self.trace = []

    # ---------------------------------------------------------------- LLM call
    def _query(self, messages, max_tokens=2048):
        """One Groq call (fresh client from the key pool). Waits and retries when rate-limited."""
        for attempt in range(4):
            try:
                reply = new_client().chat.completions.create(
                    model=self.model, messages=messages,
                    max_completion_tokens=max_tokens, reasoning_effort="medium", temperature=0, top_p=0.2)
                return reply.choices[0].message.content or ""
            except RateLimitError:
                if attempt == 3:
                    raise
                time.sleep(3 + attempt * 5)

    # ---------------------------------------------------------------- 1. choose keys at one level
    def _select_keys(self, query, keys):
        prompt = (f"USER QUERY:\n{query}\n\nAVAILABLE GRAPH KEYS:\n{json.dumps(keys, ensure_ascii=False)}\n\n"
                  f"Select the most relevant keys for answering the query (at most {self.max_branches}, "
                  "most relevant first). Use only the exact key names above. "
                  "Return [] if none is relevant. Return ONLY a JSON array of strings.")
        answer = self._query([{"role": "system", "content": "You are a precise graph navigation agent. Return JSON only."},
                              {"role": "user", "content": prompt}])
        match = re.search(r"\[.*\]", answer, re.S)
        try:
            chosen = json.loads(match.group()) if match else []
        except json.JSONDecodeError:
            chosen = []
        return list(dict.fromkeys(k for k in chosen if k in keys))[:self.max_branches]

    # ---------------------------------------------------------------- 2. walk down the graph
    def _walk(self, node, query, path, budget):
        depth = len(path)
        if budget[0] <= 0:
            self.trace.append({"kind": "limit", "path": path, "depth": depth})
            return []
        budget[0] -= 1

        if isinstance(node, dict):                                   # a branch: choose keys, go deeper
            if not node:
                return []
            keys = self._select_keys(query, list(node))
            self.trace.append({"kind": "branch", "path": path, "depth": depth,
                               "options": len(node), "selected": keys})
            return [leaf for k in keys for leaf in self._walk(node[k], query, path + [k], budget)]

        text = " ".join(map(str, node)) if isinstance(node, list) else str(node)   # a leaf: collect its text
        if not text.strip():
            return []
        self.trace.append({"kind": "leaf", "path": path, "depth": depth, "chars": len(text)})
        return [{"path": " > ".join(path), "content": text}]

    # ---------------------------------------------------------------- 3. public API
    def retrieve(self, query, graph):
        """Walk the graph for `query`. Returns the context, the formatted context text and the walk trace."""
        self.trace, t0 = [], time.perf_counter()
        context = self._walk(graph, query, [], [self.max_nodes])
        text = "\n\n".join(f"GRAPH PATH: {c['path']}\nINFORMATION: {c['content']}" for c in context)
        return {"query": query, "context": context, "context_text": text[:self.max_context_chars],
                "trace": self.trace, "elapsed_ms": round((time.perf_counter() - t0) * 1000)}

    def _generate_answer(self, query, context):
        """Write a plain-text answer from the retrieved context only."""
        text = "\n\n".join(f"GRAPH PATH: {c['path']}\nINFORMATION: {c['content']}" for c in context)
        system = ("Answer using only the retrieved graph context below. Combine the relevant branches, "
                  "organize the answer logically, never invent unsupported facts, and say when information "
                  f"is missing.\n\nRETRIEVED CONTEXT:\n{text[:self.max_context_chars]}")
        return self._query([{"role": "system", "content": system}, {"role": "user", "content": query}])


if __name__ == "__main__":      # python graph_rag.py "your question"
    import os
    import sys
    from dotenv import load_dotenv

    here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(here, ".env"))
    question = " ".join(sys.argv[1:]) or "Why did Bhishma take his vow?"
    rag = VectorlessGraphRAG()
    result = rag.retrieve(question, load_graph(os.path.join(here, "mahabharata_graph.json")))
    for item in result["context"]:
        print(f"\nPath: {item['path']}\n{item['content'][:200]}")
    print("\nANSWER\n" + rag._generate_answer(question, result["context"]))
