"""Lightweight Streamlit Monitoring Dashboard (Bonus F).

Visualizes task progress, execution DAGs, tool calls, costs, and generated artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Bodhan Agent Monitor", layout="wide", page_icon="🤖")

st.title("🤖 Bodhan Autonomous Task Agent Monitor")
st.markdown("Live monitoring for multilingual Indian AI task execution, budget tracking, and artifact verification.")

# Sidebar directory picker
out_dir_str = st.sidebar.text_input("Output Directory", value="./out")
out_dir = Path(out_dir_str).resolve()

if not out_dir.exists():
    st.warning(f"Output directory `{out_dir}` does not exist yet. Run an agent task to generate logs.")
else:
    report_file = out_dir / "report.json"
    trace_file = out_dir / "trace.jsonl"
    result_file = out_dir / "result.md"

    # Display Metrics
    if report_file.exists():
        try:
            report_data = json.loads(report_file.read_text(encoding="utf-8"))
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Task Status", report_data.get("status", "UNKNOWN").upper())
            col2.metric("Total Cost (INR)", f"₹{report_data.get('total_actual_cost_inr', 0.0):.4f}")
            col3.metric("Runtime", f"{report_data.get('total_runtime_seconds', 0.0):.2f}s")
            col4.metric(
                "Steps Completed",
                f"{report_data.get('completed_steps', 0)}/{report_data.get('total_steps', 0)}",
            )

            st.subheader("📋 Task Summary")
            st.info(f"**Task Description:** {report_data.get('task_description', '')}")

            # Verification Results
            st.subheader("✅ Verifications")
            verifs = report_data.get("verification_results", [])
            if verifs:
                for v in verifs:
                    status = v.get("status", "unknown")
                    icon = "🟢" if status == "passed" else ("🟡" if status == "partial" else "🔴")
                    st.write(f"{icon} **{v.get('check_name')}**: {v.get('details')}")
            else:
                st.write("No verification records found.")

            # Artifacts
            st.subheader("📁 Generated Artifacts")
            artifacts = report_data.get("generated_artifacts", [])
            if artifacts:
                for a in artifacts:
                    p = Path(a)
                    st.write(f"- `{p.name}` ({p.stat().st_size if p.exists() else 0} bytes)")
                    if p.suffix.lower() == ".wav" and p.exists():
                        st.audio(str(p))
                    elif p.suffix.lower() in {".txt", ".md", ".srt"} and p.exists():
                        with st.expander(f"View {p.name}"):
                            st.code(p.read_text(encoding="utf-8", errors="replace"))

        except Exception as e:
            st.error(f"Error reading report.json: {e}")

    # Trace Log Events
    if trace_file.exists():
        st.subheader("📜 Execution Trace Events")
        events = []
        try:
            for line in trace_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
            if events:
                st.dataframe(events, use_container_width=True)
        except Exception as e:
            st.error(f"Error loading trace.jsonl: {e}")
