"""Streamlit UI:  streamlit run app.py"""
import json

import streamlit as st

from llm import MODEL, make_charts

st.set_page_config(page_title="JARVIS Charts", page_icon="📊", layout="wide")
st.title("📊 JARVIS Charts")
st.caption(f"Describe the chart you want. The LLM ({MODEL}) picks the tool and arguments.")

with st.sidebar:
    show_calls = st.checkbox("Show tool calls", value=True)
    examples = ["Bar chart of the 5 most populous countries",
                "Line chart of global temperature anomaly over the last decade",
                "Pie chart of the world's continents by area",
                "Sankey of a typical household budget"]
    for e in examples:
        if st.button(e, use_container_width=True):
            st.session_state["q"] = e

query = st.chat_input("e.g. 'pie chart of the world's continents by area'")
query = query or st.session_state.pop("q", None)

if query:
    st.chat_message("user").write(query)
    with st.chat_message("assistant"):
        with st.spinner("Choosing tools…"):
            try:
                results = make_charts(query)
            except Exception as e:
                st.error(f"LLM call failed: {e}")
                results = []
        for i, r in enumerate(results):
            if r.get("text"):
                st.write(r["text"])
            elif r["error"]:
                st.error(f"`{r['tool']}` failed: {r['error']}")
            else:
                st.plotly_chart(r["figure"], use_container_width=True, key=f"c{i}")
            if show_calls and r["tool"]:
                with st.expander(f"Tool call: {r['tool']}"):
                    st.code(json.dumps(r["args"], indent=2), language="json")
