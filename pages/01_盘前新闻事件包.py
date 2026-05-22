# -*- coding: utf-8 -*-
from __future__ import annotations
import pandas as pd
import streamlit as st
from ui_utils import *
page_header("01｜盘前/盘中新闻事件包", "自动抓 AkShare 新闻、快讯、财报、公告；输出原始事件输入包，不下交易结论。")
mode = st.selectbox("模式", ["morning", "intraday", "manual", "all"], index=0)
def_hours = {"morning":12,"intraday":2,"manual":24,"all":48}[mode]
hours = st.number_input("最近多少小时", 1, 168, def_hours)
max_rows = st.number_input("最多输出条数", 20, 1000, 200, step=20)
keywords = st.text_area("关键词，可空；逗号分隔", "工业富联,中际旭创,新易盛,美图公司,小米集团-W,长鑫存储,AI算力,半导体,财报,业绩,控制权")
if st.button("运行新闻/财报/公告采集", type="primary"):
    args=["--mode",mode,"--hours",str(hours),"--max",str(max_rows),"--keywords",keywords]
    run_and_show("02_ak_news_events_plus.py", args, timeout=1800)

st.divider()
md = latest_file("*.md", ROOT/"outputs_news"/"reports")
csv = latest_file("*.csv", ROOT/"outputs_news"/"reports")
log = latest_file("*.csv", ROOT/"outputs_news"/"logs")
if csv:
    df=read_csv_any(csv)
    st.subheader("最新事件表")
    st.dataframe(df, use_container_width=True, height=380)
    c1,c2,c3=st.columns(3)
    with c1:
        if "importance" in df.columns: st.metric("最高重要度", pd.to_numeric(df["importance"],errors="coerce").max())
    with c2: st.metric("总条数", len(df))
    with c3:
        if "source" in df.columns: st.metric("来源数", df["source"].nunique())
    if px is not None and not df.empty:
        tabs=st.tabs(["重要度分布", "来源分布", "标签分布"])
        with tabs[0]:
            if "importance" in df.columns:
                tmp=df.copy(); tmp["importance"]=pd.to_numeric(tmp["importance"],errors="coerce")
                fig=px.histogram(tmp, x="importance", nbins=20, title="事件重要度分布")
                st.plotly_chart(fig, use_container_width=True)
        with tabs[1]:
            if "source" in df.columns:
                fig=px.bar(df["source"].value_counts().reset_index(), x="source", y="count", title="来源分布")
                st.plotly_chart(fig, use_container_width=True)
        with tabs[2]:
            if "tags" in df.columns:
                exploded=df.assign(tags=df["tags"].astype(str).str.split(",")).explode("tags")
                exploded=exploded[exploded["tags"].astype(str).str.len()>0]
                fig=px.bar(exploded["tags"].value_counts().head(30).reset_index(), x="tags", y="count", title="标签Top30")
                st.plotly_chart(fig, use_container_width=True)
if md:
    st.subheader("最新 Markdown 报告")
    show_file_card(md, "news_md")
if log:
    st.subheader("接口日志")
    st.dataframe(read_csv_any(log), use_container_width=True)
