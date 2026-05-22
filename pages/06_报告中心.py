# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st
from ui_utils import *
page_header("06｜报告中心", "查看、下载、合并所有 Markdown/CSV 输入包。")
patterns=[
    "outputs_news/reports/*.md", "outputs_xueqiu/xueqiu_packet_*.md", "outputs_fundflow/reports/*.md",
    "outputs_market_fundflow/reports/*.md", "outputs_overnight/reports/*.md"
]
files=list_files(patterns, ROOT, 100)
if not files:
    st.info("还没有报告。")
else:
    df=pd.DataFrame({"文件":[str(p.relative_to(ROOT)) for p in files],"修改时间":[datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S') for p in files],"大小KB":[round(p.stat().st_size/1024,1) for p in files]})
    st.dataframe(df,use_container_width=True)
    selected=st.multiselect("选择要合并的报告", files, default=files[:3], format_func=lambda p: str(p.relative_to(ROOT)))
    if st.button("合并选中报告", type="primary") and selected:
        tag=datetime.now().strftime('%Y%m%d_%H%M%S')
        out=ROOT/"outputs_combined"/f"combined_gpt_raw_packet_{tag}.md"
        parts=[]
        for p in selected:
            parts.append(f"\n\n---\n\n# 来源文件：{p.name}\n\n")
            parts.append(read_text_any(p))
        out.write_text("".join(parts), encoding="utf-8")
        st.success(f"已合并：{out}")
        show_file_card(out, "combined")
    st.divider()
    view=st.selectbox("预览单个报告", files, format_func=lambda p: str(p.relative_to(ROOT)))
    if view: show_file_card(view, "view")

st.subheader("CSV 数据文件")
csvs=list_files(["outputs_news/reports/*.csv", "outputs_xueqiu/*.csv", "outputs_fundflow/normalized/*.csv", "outputs_market_fundflow/normalized/*.csv", "outputs_overnight/normalized/*.csv"], ROOT, 100)
if csvs:
    sel=st.selectbox("选择CSV", csvs, format_func=lambda p: str(p.relative_to(ROOT)))
    if sel: show_file_card(sel, "csv")
