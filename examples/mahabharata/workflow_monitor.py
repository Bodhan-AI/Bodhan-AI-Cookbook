"""Live monitor for Claude Code background workflows (read-only).

    /opt/miniconda3/envs/onto/bin/streamlit run workflow_monitor.py

Reads, never writes:
  ~/.claude/projects/<project>/<session>/subagents/workflows/wf_*/journal.jsonl   agent started/result events
  .../wf_*/agent-<id>.jsonl (+ .meta.json)                                          each agent's live transcript
  ~/.claude/projects/<project>/<session>/workflows/scripts/*-wf_*.js               workflow name / description / phases
"""
import glob
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

PROJECTS = Path.home() / ".claude" / "projects"
PROJECT_ROOT = Path(__file__).resolve().parent
WATCH_DIRS = ["data_src/graph_parts", "web/data/voyage_parts", "web/data", "tools",
              "web/js/voyage", "web/css/voyage", "voyage_docs"]

st.set_page_config(page_title="Workflow Monitor", page_icon="🛰️", layout="wide")


# ---------------------------------------------------------------- helpers
def ago(ts):
    if ts is None:
        return "–"
    s = max(0, time.time() - ts)
    if s < 60:
        return f"{int(s)}s ago"
    if s < 3600:
        return f"{int(s // 60)}m {int(s % 60)}s ago"
    return f"{int(s // 3600)}h {int(s % 3600 // 60)}m ago"


def dur(a, b):
    if a is None or b is None:
        return "–"
    s = max(0, b - a)
    return f"{int(s // 60)}m {int(s % 60):02d}s"


def iso_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def summarize_input(name, inp):
    """One readable line for a tool call."""
    if not isinstance(inp, dict):
        return str(inp)[:160]
    for k in ("file_path", "path", "pattern", "url", "query", "description"):
        if inp.get(k):
            v = str(inp[k]).replace(str(PROJECT_ROOT) + "/", "")
            extra = f" — {inp['description']}" if k != "description" and inp.get("description") else ""
            return f"{v}{extra}"[:200]
    if inp.get("command"):
        return " ".join(str(inp["command"]).split())[:200]
    return json.dumps(inp)[:160]


# ---------------------------------------------------------------- discovery
def find_runs():
    runs = []
    for j in glob.glob(str(PROJECTS / "*" / "*" / "subagents" / "workflows" / "wf_*" / "journal.jsonl")):
        d = Path(j).parent
        session_dir = d.parents[2]
        mtime = max((p.stat().st_mtime for p in d.iterdir()), default=os.path.getmtime(j))
        runs.append({"id": d.name, "dir": d, "session_dir": session_dir, "mtime": mtime})
    return sorted(runs, key=lambda r: r["mtime"], reverse=True)


@st.cache_data(show_spinner=False)
def script_meta(session_dir: str, run_id: str, _mtime: float):
    for p in glob.glob(os.path.join(session_dir, "workflows", "scripts", f"*{run_id}*.js")):
        text = Path(p).read_text(errors="replace")
        m = re.search(r"export const meta\s*=\s*\{(.*?)\n\}", text, re.S)
        block = m.group(1) if m else text[:3000]
        name = re.search(r"name:\s*'([^']+)'", block)
        desc = re.search(r"description:\s*'([^']+)'", block)
        phases = re.findall(r"\{\s*title:\s*'([^']+)'(?:,\s*detail:\s*'([^']*)')?", block)
        return {"name": name.group(1) if name else run_id, "description": desc.group(1) if desc else "",
                "phases": [{"title": t, "detail": dt} for t, dt in phases], "script": p}
    return {"name": run_id, "description": "", "phases": [], "script": None}


def read_journal(path: Path):
    events = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # half-written last line
    return events


@st.cache_data(show_spinner=False, max_entries=256)
def parse_transcript(path: str, _mtime: float, _size: int):
    """Activity feed + stats from an agent transcript (cached until the file changes)."""
    first = last = None
    tools = Counter()
    feed = []
    model = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = iso_ts(d["timestamp"]) if d.get("timestamp") else None
            if ts:
                first = first or ts
                last = ts
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            model = msg.get("model") or model
            for b in msg.get("content") or []:
                if b.get("type") == "tool_use":
                    tools[b.get("name", "?")] += 1
                    feed.append({"time": ts, "kind": "tool", "name": b.get("name", "?"),
                                 "detail": summarize_input(b.get("name"), b.get("input"))})
                elif b.get("type") == "text" and (b.get("text") or "").strip():
                    feed.append({"time": ts, "kind": "text", "name": "says",
                                 "detail": " ".join(b["text"].split())[:300]})
    return {"first": first, "last": last, "tools": dict(tools), "feed": feed[-60:], "model": model}


def build_agents(run):
    events = read_journal(run["dir"] / "journal.jsonl")
    agents, order, other = {}, [], []
    for ev in events:
        t, aid = ev.get("type"), ev.get("agentId")
        if t == "started" and aid:
            agents[aid] = {"id": aid, "label": ev.get("label") or aid, "phase": ev.get("phase") or "",
                           "status": "running", "result": None, "tools_used": None}
            order.append(aid)
        elif t == "result" and aid in agents:
            agents[aid].update(status="done", result=ev.get("result"), tools_used=ev.get("toolsUsed"))
        elif aid in agents and t and t not in ("started", "result"):
            agents[aid]["status"] = t  # e.g. failed / skipped / error, if the harness logs them
        elif t not in ("launched",):
            other.append(ev)
    for aid in order:
        a = agents[aid]
        tp = run["dir"] / f"agent-{aid}.jsonl"
        if tp.exists():
            stt = tp.stat()
            a["transcript"] = parse_transcript(str(tp), stt.st_mtime, stt.st_size)
            a["size"], a["mtime"] = stt.st_size, stt.st_mtime
        else:
            a["transcript"], a["size"], a["mtime"] = None, 0, None
        if a["status"] == "running" and a["mtime"] and time.time() - a["mtime"] > 600:
            a["status"] = "stalled?"
    return [agents[a] for a in order], events, other


# ---------------------------------------------------------------- UI
st.sidebar.title("🛰️ Workflow Monitor")
runs = find_runs()
if not runs:
    st.info(f"No workflow runs found under {PROJECTS}.")
    st.stop()

labels = {r["id"]: f"{script_meta(str(r['session_dir']), r['id'], r['mtime'])['name']} · {r['id']} · {ago(r['mtime'])}"
          for r in runs}
default = [r["id"] for r in runs if time.time() - r["mtime"] < 2 * 3600][:4] or [runs[0]["id"]]
chosen = st.sidebar.multiselect("Runs", [r["id"] for r in runs], default=default, format_func=lambda i: labels[i])
refresh = st.sidebar.select_slider("Auto-refresh", options=[0, 2, 5, 10, 30], value=5,
                                   format_func=lambda s: "off" if s == 0 else f"every {s}s")
follow = st.sidebar.checkbox("Auto-follow new runs (active in the last 15 min)", value=True,
                             help="Runs started after this page loaded are shown automatically, newest first.")
feed_n = st.sidebar.slider("Activity lines per agent", 5, 60, 15)
show_files = st.sidebar.checkbox("Show project files being produced", value=True)
st.sidebar.caption("Read-only: this page only reads Claude Code's local run logs.")

STATUS_ICON = {"running": "🟡", "done": "🟢", "stalled?": "🟠"}


def render_run(run):
    meta = script_meta(str(run["session_dir"]), run["id"], run["mtime"])
    agents, events, other = build_agents(run)
    n_run = sum(a["status"] == "running" for a in agents)
    n_done = sum(a["status"] == "done" for a in agents)
    finished = any(e.get("type") in ("completed", "finished", "done") for e in other) or (agents and n_run == 0)

    idle = time.time() - run["mtime"]
    state = ("✅ done" if finished and n_done == len(agents)
             else "⏹ stopped / idle" if idle > 10 * 60 else "⏳ running")
    st.subheader(f"{state} — {meta['name']}  ·  `{run['id']}`")
    if meta["description"]:
        st.caption(meta["description"])
    c = st.columns(5)
    c[0].metric("Agents started", len(agents))
    c[1].metric("Running", n_run)
    c[2].metric("Done", n_done)
    c[3].metric("Other", len(agents) - n_run - n_done)
    c[4].metric("Last activity", ago(run["mtime"]))

    # phases
    if meta["phases"]:
        cols = st.columns(len(meta["phases"]))
        for col, ph in zip(cols, meta["phases"]):
            pa = [a for a in agents if a["phase"] == ph["title"]]
            dn = sum(a["status"] == "done" for a in pa)
            col.markdown(f"**{ph['title']}** — {dn}/{len(pa)} done")
            col.progress(dn / len(pa) if pa else 0.0)
            if ph["detail"]:
                col.caption(ph["detail"])

    # agent table
    rows = []
    for a in agents:
        tr = a["transcript"] or {}
        last_act = (tr.get("feed") or [{}])[-1]
        rows.append({
            "": STATUS_ICON.get(a["status"], "🔴"), "agent": a["label"], "phase": a["phase"], "status": a["status"],
            "runtime": dur(tr.get("first"), tr.get("last") if a["status"] != "running" else time.time()),
            "tool calls": sum((tr.get("tools") or {}).values()),
            "last activity": ago(a["mtime"]),
            "now doing": f"{last_act.get('name', '')}: {last_act.get('detail', '')}"[:120] if last_act else "",
            "transcript": size(a["size"]),
        })
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)

    # per-agent detail
    for a in agents:
        tr = a["transcript"] or {}
        with st.expander(f"{STATUS_ICON.get(a['status'], '🔴')} {a['label']}  ·  {a['phase']}  ·  "
                         f"{sum((tr.get('tools') or {}).values())} tool calls", expanded=False):
            if tr.get("tools"):
                st.caption("Tools: " + ", ".join(f"{k} ×{v}" for k, v in sorted(tr["tools"].items(), key=lambda x: -x[1])))
            for item in (tr.get("feed") or [])[-feed_n:]:
                t = datetime.fromtimestamp(item["time"]).strftime("%H:%M:%S") if item["time"] else "--:--:--"
                icon = "🔧" if item["kind"] == "tool" else "💬"
                st.markdown(f"`{t}` {icon} **{item['name']}** — {item['detail']}")
            if a["result"]:
                st.markdown("**Result**")
                st.code(a["result"] if isinstance(a["result"], str) else json.dumps(a["result"], indent=2), language=None)
            st.caption(f"agent id {a['id']} · transcript {run['dir'] / ('agent-' + a['id'] + '.jsonl')}")


def render_files():
    st.subheader("📁 Files being produced")
    rows = []
    for d in WATCH_DIRS:
        base = PROJECT_ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and (p.parent == base or d.startswith("web/js")):
                s = p.stat()
                rows.append({"file": str(p.relative_to(PROJECT_ROOT)), "size": size(s.st_size),
                             "modified": ago(s.st_mtime), "_m": s.st_mtime})
    rows.sort(key=lambda r: -r["_m"])
    for r in rows:
        r.pop("_m")
    st.dataframe(rows, width="stretch", hide_index=True) if rows else st.caption("Nothing yet.")


@st.fragment(run_every=refresh if refresh else None)
def live():
    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")
    fresh = find_runs()
    by_id = {r["id"]: r for r in fresh}
    active = [r["id"] for r in fresh if time.time() - r["mtime"] < 15 * 60] if follow else []
    for rid in list(dict.fromkeys(active + list(chosen))):  # newest active runs first, then the chosen ones
        if rid in by_id:
            render_run(by_id[rid])
            st.divider()
    if show_files:
        render_files()


live()
