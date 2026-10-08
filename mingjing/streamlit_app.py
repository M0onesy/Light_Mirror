from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

path = Path(os.environ.get("MINGJING_RESULTS", "results.json"))
data = json.loads(path.read_text(encoding="utf-8"))
st.set_page_config(page_title="明镜 MingJing", layout="wide")
st.title("明镜 MingJing")
st.caption(f"运行 ID：{data.get('run_id', '')}")
summary = data.get("summary", {})
cols = st.columns(3)
for col, key in zip(cols, ("verified", "violated", "abstained")):
    col.metric(key, summary.get(key, 0))
left, middle, right = st.columns((1.2, 1, 1))
with left:
    st.subheader("原子陈述")
    for item in data.get("results", []):
        st.markdown(f"**{item['claim_id']}** · `{item['status']}`")
        st.write(item.get("claim_text", ""))
with middle:
    st.subheader("核查结果")
    for item in data.get("results", []):
        with st.expander(f"{item['claim_id']} · {item['status']}"):
            st.json(item)
with right:
    st.subheader("证据与修改建议")
    for item in data.get("results", []):
        if item.get("evidence"):
            st.write(item["claim_id"], item["evidence"])
        if item.get("correction"):
            st.json(item["correction"])

