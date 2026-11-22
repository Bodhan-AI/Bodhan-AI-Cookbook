"""The agent loop: preflight -> plan -> (act -> observe)* -> verify gate -> finish, inside hard budgets."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from bodhan_mcp.langs import SERVICES, supported_list

from . import local_tools
from .backend import InProcessBackend, McpStdioBackend
from .llm import LLM, LLMError, LLMToolCallError
from .prompts import SYSTEM, capabilities_note
from .trace import Trace

UNTRUSTED_KEYS = {"preview", "sample_text", "heard_preview"}
MAX_TOOL_MSG = 3500


def _shape(result: dict[str, Any]) -> str:
    r = dict(result)
    for k in UNTRUSTED_KEYS:
        if r.get(k):
            r[k] = local_tools.untrusted(str(r[k]))
    s = json.dumps(r, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TOOL_MSG else s[:MAX_TOOL_MSG] + "…[truncated]"


def _summary(result: dict[str, Any], n: int = 300) -> str:
    keep = {k: v for k, v in result.items() if k not in UNTRUSTED_KEYS and not isinstance(v, (list, dict))}
    for k in ("issues", "problems", "warnings", "needs_review"):
        if result.get(k):
            keep[k] = result[k]
    s = json.dumps(keep, ensure_ascii=False, default=str)
    return s[:n] + ("…" if len(s) > n else "")


class Agent:
    def __init__(self, task: str, files: list[str], out_dir: Path, llm: Any, backend: Any | None = None,
                 max_steps: int = 30, max_cost_inr: float = 20.0, max_seconds: float = 600,
                 call_timeout_s: float = 180, use_mcp: bool = False):
        self.task, self.files = task, files
        self.out = out_dir.resolve()
        self.art = self.out / "artifacts"
        self.llm = llm
        self.backend = backend or (McpStdioBackend() if use_mcp else InProcessBackend())
        self.max_steps, self.max_cost, self.max_seconds, self.call_timeout = max_steps, max_cost_inr, max_seconds, call_timeout_s
        self.steps = 0
        self.spent = 0.0
        self.cost_by_tool: dict[str, float] = {}
        self.tool_log: list[dict[str, Any]] = []
        self.run_cache: dict[str, dict[str, Any]] = {}
        self.speech: dict[str, dict[str, Any]] = {}
        self.translations: dict[str, dict[str, Any]] = {}
        self.verifications: list[dict[str, Any]] = []
        self.llm_tokens = {"prompt": 0, "completion": 0, "calls": 0}
        self.final: dict[str, Any] | None = None
        self.stop_reason: str | None = None
        self.error_message: str | None = None
        self.t0 = time.monotonic()

    # ------------------------------------------------------------------ helpers
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _budget_stop(self) -> str | None:
        if self.steps >= self.max_steps:
            return "max_steps"
        if self.spent >= self.max_cost - 1e-9:
            return "max_cost"
        if self.elapsed() >= self.max_seconds:
            return "max_seconds"
        return None

    async def _call_backend(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            res = await asyncio.wait_for(self.backend.call(name, args), self.call_timeout)
        except asyncio.TimeoutError:
            res = {"ok": False, "code": "tool_timeout", "retryable": True,
                   "message": f"{name} exceeded the {self.call_timeout:.0f}s per-call timeout."}
        res.setdefault("latency_s", round(time.monotonic() - t0, 2))
        return res

    def _account(self, name: str, res: dict[str, Any]) -> None:
        c = float(res.get("cost_inr") or 0)
        self.spent += c
        self.cost_by_tool[name] = self.cost_by_tool.get(name, 0.0) + c

    # ------------------------------------------------------------------ preflight
    def _expand_files(self) -> list[str]:
        paths: list[str] = []
        for f in self.files:
            p = Path(f).expanduser()
            if p.is_dir():
                paths += [str(x.resolve()) for x in sorted(p.rglob("*")) if x.is_file() and not x.name.startswith(".")]
            else:
                paths.append(str(p.resolve()) if p.exists() else f)
        return paths[:60]

    async def _preflight(self) -> list[str]:
        lines = []
        for path in self._expand_files():
            if not Path(path).exists():
                lines.append(f"- {path}: MISSING (file does not exist)")
                self.trace.log("preflight", path=path, ok=False, error="missing")
                continue
            r = await self._call_backend("inspect_file", {"path": path})
            self.trace.log("preflight", path=path, ok=r.get("ok", False), result=_summary(r))
            if not r.get("ok"):
                lines.append(f"- {path}: unreadable ({r.get('message')})")
                continue
            bits = [r["kind"]] + [f"{k}={r[k]}" for k in ("duration_s", "pages", "width", "height", "chars") if r.get(k) is not None]
            if r.get("has_audio") is False:
                bits.append("NO AUDIO TRACK")
            lines.append(f"- {path} ({', '.join(bits)})")
        return lines

    # ------------------------------------------------------------------ one tool call
    async def _exec(self, tc: Any, allowed_roots: list[Path]) -> str:
        name = tc.name
        try:
            args = json.loads(tc.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            res = {"ok": False, "code": "invalid_arguments", "message": f"Could not parse arguments: {e}"}
            self.trace.log("tool_call", tool=name, arguments=tc.arguments[:300], ok=False, result=_summary(res))
            return _shape(res)
        self.steps += 1
        if name == "finish":
            return await self._handle_finish(args)
        t0 = time.monotonic()
        if name == "read_text_file":
            res = local_tools.read_text_file(args.get("path", ""), int(args.get("offset") or 0),
                                             int(args.get("max_chars") or 6000), allowed_roots)
        elif name == "write_text_file":
            res = local_tools.write_text_file(args.get("filename", ""), args.get("content", ""), self.art)
        else:
            key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if key in self.run_cache:
                res = {**self.run_cache[key], "cost_inr": 0.0, "api_calls": 0,
                       "note": "Identical call already made in this run; reused its result at no cost."}
            else:
                res = await self._call_backend(name, args)
                if res.get("ok"):
                    self.run_cache[key] = res
        res.setdefault("latency_s", round(time.monotonic() - t0, 2))
        self._account(name, res)
        self._track(name, args, res)
        rec = {"tool": name, "arguments": args, "ok": res.get("ok", False), "latency_s": res.get("latency_s"),
               "cost_inr": round(float(res.get("cost_inr") or 0), 4)}
        self.tool_log.append(rec)
        self.trace.log("tool_call", step=self.steps, tool=name, arguments=json.dumps(args, ensure_ascii=False)[:400],
                       ok=res.get("ok", False), result=_summary(res), latency_s=res.get("latency_s"),
                       cost_inr=rec["cost_inr"], cumulative_cost_inr=round(self.spent, 4))
        if res.get("code") == "budget_exceeded":
            self.stop_reason = "max_cost"
        return _shape(res)

    def _track(self, name: str, args: dict, res: dict) -> None:
        if not res.get("ok"):
            return
        if name == "synthesize_speech":
            self.speech[res["audio_path"]] = {"language": res["language"], "spoken_text_path": res["spoken_text_path"],
                                              "verified": None}
        elif name == "translate_text":
            self.translations[res["translation_path"]] = {"language": res["target_language"],
                                                          "source_path": res["source_path"], "verified": None}
        elif name == "verify_speech" and args.get("audio_path") in self.speech:
            self.speech[args["audio_path"]]["verified"] = {"ok": res.get("ok") and not res.get("issues"), **_pick(res)}
        elif name == "verify_translation" and args.get("translation_path") in self.translations:
            self.translations[args["translation_path"]]["verified"] = {"ok": not res.get("issues"), **_pick(res)}
        elif name == "translate_segments":
            self.verifications.append({"kind": "translate_segments", "target": res.get("segments_path"),
                                       "ok": not res.get("problems"), "details": res.get("problems", [])[:5],
                                       "method": "built-in per-segment checks"})

    # ------------------------------------------------------------------ verification gate
    async def _verify_pending(self) -> list[str]:
        jobs: list[tuple[str, str, str, dict]] = []
        for audio_path, info in self.speech.items():
            if info["verified"] is None and Path(audio_path).exists():
                jobs.append(("speech", audio_path, "verify_speech",
                             {"audio_path": audio_path, "language": info["language"],
                              "expected_path": info["spoken_text_path"]}))
        for tpath, info in self.translations.items():
            if info["verified"] is None:
                try:
                    small = len(Path(info["source_path"]).read_text(encoding="utf-8")) <= 3000
                except OSError:
                    small = False
                jobs.append(("translation", tpath, "verify_translation",
                             {"target_language": info["language"], "translation_path": tpath,
                              "source_path": info["source_path"], "back_translate": small}))
        if not jobs:
            return []
        results = await asyncio.gather(*[self._call_backend(n, a) for _, _, n, a in jobs])
        failures = []
        for (kind, target, name, _), res in zip(jobs, results):
            self._account(name, res)
            ok = bool(res.get("ok")) and not res.get("issues")
            store = self.speech if kind == "speech" else self.translations
            store[target]["verified"] = {"ok": ok, **_pick(res), "error": None if res.get("ok") else res.get("message")}
            self.trace.log("verify", kind=kind, target=target, ok=ok, result=_summary(res),
                           cost_inr=round(float(res.get("cost_inr") or 0), 4), auto=True)
            if not ok:
                reason = "; ".join(res.get("issues") or []) or res.get("message") or "verification failed"
                failures.append(f"{kind} {Path(target).name}: {reason}")
        return failures

    def _known_failures(self, delivered: list[str]) -> list[str]:
        """Outputs that were verified earlier and failed (and are still being delivered)."""
        out = []
        for kind, store in (("speech", self.speech), ("translation", self.translations)):
            for path, info in store.items():
                v = info["verified"]
                if v and not v.get("ok") and (not delivered or path in delivered):
                    reason = "; ".join(v.get("issues") or []) or v.get("error") or "verification failed"
                    out.append(f"{kind} {Path(path).name}: {reason}")
        return out

    def _format_gaps(self, artifacts: list[str]) -> list[str]:
        """Deliverables whose FORMAT the task names explicitly (e.g. subtitles must be a timed .srt/.vtt file)."""
        gaps = []
        if re.search(r"\b(subtitles?|captions?|srt|vtt)\b", self.task, re.I) and \
                not any(Path(a).suffix.lower() in (".srt", ".vtt") for a in artifacts):
            gaps.append("deliverable: the task asks for subtitles, so a timed .srt/.vtt file must be among the artifacts "
                        "(transcribe_audio -> translate_segments -> write_subtitles). A plain .txt is not subtitles.")
        return gaps

    async def _handle_finish(self, args: dict[str, Any]) -> str:
        status = args.get("status", "partial")
        if status not in ("completed", "partial", "impossible"):
            status = "partial"
        arts = [a for a in (args.get("artifacts") or []) if isinstance(a, str)]
        notes: list[str] = []
        if status != "impossible":
            if self.stop_reason is None:
                await self._verify_pending()
                failures = self._known_failures(arts)
                if status == "completed":
                    failures += self._format_gaps(arts)
                if failures and self.gate_rounds < 2:
                    self.gate_rounds += 1
                    msg = ("VERIFICATION FAILED, so the task is not finished yet:\n- " + "\n- ".join(failures) +
                           "\nFix it (for example resynthesize with another voice or shorter text, or re-translate), "
                           "or call finish again with status 'partial' and describe the problem honestly.")
                    self.trace.log("verify_gate", round=self.gate_rounds, failures=failures)
                    return json.dumps({"ok": False, "code": "verification_failed", "message": msg}, ensure_ascii=False)
                if failures:
                    status = "partial"
                    notes.append("Verification still failing after retries: " + "; ".join(failures))
            else:
                if any(i["verified"] is None for i in list(self.speech.values()) + list(self.translations.values())):
                    notes.append("Some outputs could not be verified because a budget ran out.")
            missing = [a for a in arts if not Path(a).exists()]
            if missing:
                status = "partial"
                notes.append(f"Listed artifacts that do not exist: {missing}")
                arts = [a for a in arts if Path(a).exists()]
        else:
            arts = []
        self.final = {"status": status, "summary": args.get("summary", ""), "artifacts": arts,
                      "assumptions": [a for a in (args.get("assumptions") or []) if isinstance(a, str)],
                      "notes": notes}
        self.trace.log("finish", **{k: v for k, v in self.final.items()})
        return json.dumps({"ok": True, "message": "Task finished."})

    # ------------------------------------------------------------------ main loop
    async def run(self) -> dict[str, Any]:
        self.art.mkdir(parents=True, exist_ok=True)
        self.trace = Trace(self.out / "trace.jsonl")
        self.gate_rounds = 0
        env = {"BODHAN_WORK_DIR": str(self.art), "BODHAN_MAX_COST_INR": str(self.max_cost)}
        os.environ.update(env)
        try:
            from bodhan_mcp.costs import tracker
            tracker.reset(self.max_cost)
        except ImportError:
            tracker = None  # noqa: F841
        try:
            await self.backend.start(env)
            tools = await self.backend.list_tools() + local_tools.LOCAL_TOOLS
            files = await self._preflight()
            allowed = [self.art] + [Path(f).parent for f in self._expand_files()] + [Path(f) for f in self.files if Path(f).is_dir()]
            inventory = "\n".join(files) or "(no input files were provided)"
            langs = {s: supported_list(s) for s in SERVICES}
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": SYSTEM + "\n" + capabilities_note(langs)},
                {"role": "user", "content": (
                    f"TASK: {self.task}\n\nINPUT FILES:\n{inventory}\n\n"
                    f"Output folder for files you create: {self.art}\n"
                    f"Budgets: at most {self.max_steps} tool calls, ₹{self.max_cost:.2f}, {self.max_seconds:.0f}s.\n\n"
                    "First reply with a SHORT plan in plain text (numbered steps, and say if the task looks impossible "
                    "and why). Do not call tools yet.")}]
            try:
                plan = await self.llm.chat(messages, tools=None)
                self._count(plan)
            except LLMError as e:
                self.stop_reason = "llm_error"
                self.error_message = str(e)
                self.trace.log("error", message=str(e))
                return self._finalize()
            self.trace.log("plan", plan=plan.content)
            messages += [{"role": "assistant", "content": plan.content or "(no plan)"},
                         {"role": "user", "content": "Go ahead and execute the plan with tool calls. "
                                                      "End with the `finish` tool."}]
            nudges = consecutive_fail = bad_calls = 0
            while self.final is None and self.stop_reason is None:
                stop = self._budget_stop()
                if stop:
                    self.stop_reason = stop
                    break
                try:
                    reply = await self.llm.chat(messages, tools=tools)
                    self._count(reply)
                except LLMToolCallError as e:
                    bad_calls += 1
                    self.trace.log("llm_retry", reason="malformed tool call", message=str(e)[:500], attempt=bad_calls)
                    if bad_calls > 4:
                        self.stop_reason = "llm_error"
                        self.error_message = str(e)
                        break
                    messages.append({"role": "user", "content": (
                        "Your last tool call was rejected as malformed. Call the tool again with valid JSON arguments "
                        "that match its schema exactly: use only documented parameters, omit optional parameters you "
                        "do not need (never pass null or empty strings), and use real numbers for numeric fields.")})
                    continue
                except LLMError as e:
                    self.stop_reason = "llm_error"
                    self.error_message = str(e)
                    self.trace.log("error", message=str(e))
                    break
                self.trace.log("llm", content=(reply.content or "")[:600], tool_calls=[c.name for c in reply.tool_calls])
                if not reply.tool_calls:
                    nudges += 1
                    if nudges > 2:
                        self.final = {"status": "partial", "summary": reply.content or "The model stopped without finishing.",
                                      "artifacts": [], "assumptions": [], "notes": ["Model ended without calling finish."]}
                        break
                    messages += [{"role": "assistant", "content": reply.content or ""},
                                 {"role": "user", "content": "Call a tool, or call `finish` if you are done."}]
                    continue
                messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in reply.tool_calls]})
                room = self.max_steps - self.steps
                run_now, skipped = reply.tool_calls[:max(0, room)], reply.tool_calls[max(0, room):]
                finish_calls = [c for c in run_now if c.name == "finish"]
                others = [c for c in run_now if c.name != "finish"]
                outs = await asyncio.gather(*[self._exec(c, allowed) for c in others])
                results = dict(zip([c.id for c in others], outs))
                for c in finish_calls:  # finish runs after sibling calls so it sees their results
                    results[c.id] = await self._exec(c, allowed)
                for c in skipped:
                    results[c.id] = json.dumps({"ok": False, "code": "step_budget_exhausted",
                                                "message": "No steps left; call finish with what you have."})
                for c in reply.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": results[c.id]})
                failed = [json.loads(results[c.id]).get("ok") is False for c in others if results[c.id].startswith("{")]
                consecutive_fail = consecutive_fail + 1 if failed and all(failed) else 0
                if consecutive_fail >= 3:
                    messages.append({"role": "user", "content": "Three calls in a row failed. Re-plan with a different "
                                     "approach, or call `finish` with status 'partial'/'impossible' and explain."})
                    consecutive_fail = 0
            if self.final is None and self.stop_reason:
                await self._wrap_up_after_stop()
        finally:
            try:
                await self.backend.close()
            except Exception:  # noqa: BLE001
                pass
        return self._finalize()

    def _count(self, reply: Any) -> None:
        self.llm_tokens["prompt"] += getattr(reply, "prompt_tokens", 0)
        self.llm_tokens["completion"] += getattr(reply, "completion_tokens", 0)
        self.llm_tokens["calls"] += 1

    async def _wrap_up_after_stop(self) -> None:
        made = sorted(str(p) for p in self.art.rglob("*") if p.is_file() and "intermediate" not in p.parts)
        self.trace.log("budget_stop", reason=self.stop_reason, steps=self.steps, spent_inr=round(self.spent, 4),
                       elapsed_s=round(self.elapsed(), 1))
        done = [f"{t['tool']}({'ok' if t['ok'] else 'failed'})" for t in self.tool_log]
        self.final = {"status": "partial", "artifacts": made, "assumptions": [],
                      "summary": (f"Stopped early because of the budget: {self.stop_reason}. "
                                  f"Completed {len(self.tool_log)} tool calls ({', '.join(done) or 'none'}), "
                                  f"spent ₹{self.spent:.2f}. The task is NOT complete; files listed below are partial results."),
                      "notes": []}

    # ------------------------------------------------------------------ outputs
    def _finalize(self) -> dict[str, Any]:
        fin = self.final or {"status": "failed",
                             "summary": f"The run ended early ({self.stop_reason}). {self.error_message or ''}".strip(),
                             "artifacts": [], "assumptions": [], "notes": []}
        if self.stop_reason and fin["status"] == "completed":
            fin["status"] = "partial"
        ver = [{"kind": "speech", "target": k, **v["verified"]} for k, v in self.speech.items() if v["verified"]] + \
              [{"kind": "translation", "target": k, **v["verified"]} for k, v in self.translations.items() if v["verified"]] + \
              self.verifications
        unverified = [k for k, v in {**self.speech, **self.translations}.items() if v["verified"] is None]
        dup = None
        try:
            from bodhan_mcp.costs import tracker
            if self.backend.name == "in-process":
                dup = tracker.duplicate_requests()
        except ImportError:
            pass
        report = {
            "status": fin["status"], "task": self.task, "stop_reason": self.stop_reason, "error": self.error_message,
            "steps": self.steps, "max_steps": self.max_steps,
            "elapsed_s": round(self.elapsed(), 1),
            "total_cost_inr": round(self.spent, 4), "max_cost_inr": self.max_cost,
            "cost_by_tool_inr": {k: round(v, 4) for k, v in self.cost_by_tool.items()},
            "llm": self.llm_tokens, "backend": self.backend.name,
            "verification": ver, "unverified_outputs": unverified,
            "artifacts": fin["artifacts"], "assumptions": fin["assumptions"], "notes": fin["notes"],
            "tool_calls": self.tool_log,
            "targets": {
                "budget_respected": self.spent <= self.max_cost + 1e-6,
                "all_outputs_verified": not unverified,
                "duplicate_model_requests": dup,
                "latency_under_5min": self.elapsed() < 300,
            },
        }
        (self.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.out / "result.md").write_text(self._result_md(fin, ver, report), encoding="utf-8")
        self.trace.log("done", status=fin["status"], cost_inr=report["total_cost_inr"], elapsed_s=report["elapsed_s"])
        self.trace.close()
        return report

    def _result_md(self, fin: dict, ver: list, report: dict) -> str:
        L = [f"# Result: {fin['status'].upper()}", "", f"**Task:** {self.task}", "", "## Summary", "", fin["summary"] or "(none)", ""]
        if fin["status"] == "impossible":
            L += ["_No output was produced because the task cannot be done with the available tools._", ""]
        if fin["artifacts"]:
            L += ["## Artifacts", ""]
            for a in fin["artifacts"]:
                rel = os.path.relpath(a, self.out)
                L.append(f"- [{rel}]({rel})" + (" (AI-generated audio)" if Path(a).suffix.lower() in (".mp3", ".wav", ".ogg") else ""))
            L.append("")
        if fin["assumptions"]:
            L += ["## Assumptions", ""] + [f"- {a}" for a in fin["assumptions"]] + [""]
        if fin["notes"]:
            L += ["## Notes", ""] + [f"- {n}" for n in fin["notes"]] + [""]
        if ver:
            L += ["## Verification", ""] + [f"- {v['kind']} `{Path(str(v['target'])).name}`: {'PASS' if v.get('ok') else 'FAIL'}"
                                            + (f" (CER {v['cer']})" if v.get("cer") is not None else "") for v in ver] + [""]
        L += ["## Cost and time", "", f"₹{report['total_cost_inr']:.2f} of ₹{report['max_cost_inr']:.2f}; "
              f"{report['steps']} tool calls; {report['elapsed_s']}s. Details: `report.json`, `trace.jsonl`."]
        return "\n".join(L) + "\n"


def _pick(res: dict) -> dict:
    return {k: res[k] for k in ("cer", "length_ratio", "issues", "back_translation_coverage", "repetitions", "omissions") if k in res}
