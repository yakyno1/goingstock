# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_utils import *


page_header("01｜盘前/盘中新闻事件包", "AKShare 最新财经新闻 + 雪球 7x24 快讯用户时间线。")

st.subheader("AKShare 最新新闻")
st.caption("直接调用 ak.stock_info_global_em()，保存接口返回的最新表格。")
if st.button("采集 AKShare 最新200条新闻", type="primary", use_container_width=True):
    run_and_show("02_ak_news_events_plus.py", ["--source", "ak"], timeout=1800)

st.divider()

st.subheader("雪球 7x24 用户快讯")
c1, c2, c3 = st.columns(3)
with c1:
    xueqiu_hours = st.number_input("最近多少小时", 1, 168, 24)
with c2:
    xueqiu_pages = st.number_input("雪球采集页数", 1, 100, 20)
with c3:
    xueqiu_max_rows = st.number_input("最多输出条数", 20, 2000, 300, step=20)

xueqiu_user_id = st.text_input("雪球用户ID", "5124430882")
xueqiu_keywords = st.text_area("雪球关键词过滤，可空；逗号分隔", "")

if st.button("采集雪球 7x24 用户快讯", type="primary", use_container_width=True):
    args = [
        "--source", "xueqiu",
        "--hours", str(xueqiu_hours),
        "--pages", str(xueqiu_pages),
        "--max", str(xueqiu_max_rows),
        "--keywords", xueqiu_keywords,
        "--xueqiu-user-id", xueqiu_user_id,
    ]
    run_and_show("02_ak_news_events_plus.py", args, timeout=2400)

st.divider()


def show_metrics(df: pd.DataFrame, source_label: str) -> None:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("总条数", len(df))
    with c2:
        st.metric("数据表", source_label)
    with c3:
        if "importance" in df.columns:
            st.metric("最高重要度", pd.to_numeric(df["importance"], errors="coerce").max())
    with c4:
        if "time" in df.columns and not df.empty:
            times = df["time"].dropna().astype(str)
            if not times.empty:
                st.metric("最新时间", str(times.max())[:16])


def show_log(log_path: Path | None, key: str) -> None:
    if not log_path:
        return
    with st.expander("接口日志", expanded=False):
        st.dataframe(read_csv_any(log_path), use_container_width=True)
        file_download("下载日志", log_path, "text/csv")


def show_report(md_path: Path | None, key: str) -> None:
    if not md_path:
        return
    with st.expander("Markdown 报告", expanded=False):
        show_file_card(md_path, key)


def show_xueqiu_charts(df: pd.DataFrame) -> None:
    if px is None or df.empty:
        return
    tabs = st.tabs(["时间线", "来源分布", "标签分布"])
    with tabs[0]:
        show = df.copy()
        if "time" in show.columns and "importance" in show.columns:
            show["time_dt"] = pd.to_datetime(show["time"], errors="coerce")
            show["importance"] = pd.to_numeric(show["importance"], errors="coerce")
            show = show.dropna(subset=["time_dt"])
            if not show.empty:
                fig = px.scatter(
                    show,
                    x="time_dt",
                    y="importance",
                    color="source" if "source" in show.columns else None,
                    hover_data=[c for c in ["title", "tags"] if c in show.columns],
                    title="雪球快讯时间线",
                )
                st.plotly_chart(fig, use_container_width=True)
    with tabs[1]:
        if "source" in df.columns:
            vc = df["source"].value_counts().reset_index()
            fig = px.bar(vc, x="source", y="count", title="来源分布")
            st.plotly_chart(fig, use_container_width=True)
    with tabs[2]:
        if "tags" in df.columns:
            exploded = df.assign(tags=df["tags"].astype(str).str.split(",")).explode("tags")
            exploded = exploded[exploded["tags"].astype(str).str.len() > 0]
            if not exploded.empty:
                vc = exploded["tags"].value_counts().head(30).reset_index()
                fig = px.bar(vc, x="tags", y="count", title="标签Top30")
                st.plotly_chart(fig, use_container_width=True)


st.subheader("AKShare 最新表")
ak_csv = latest_file("news_events_ak_*.csv", ROOT / "outputs_news" / "reports")
ak_md = latest_file("news_events_ak_*.md", ROOT / "outputs_news" / "reports")
ak_log = latest_file("news_events_ak_*_log.csv", ROOT / "outputs_news" / "logs")
if ak_csv:
    ak_df = read_csv_any(ak_csv)
    show_metrics(ak_df, "AKShare")
    st.dataframe(ak_df, use_container_width=True, height=420)
    file_download("下载 AKShare CSV", ak_csv, "text/csv")
    show_report(ak_md, "ak_md")
    show_log(ak_log, "ak_log")
else:
    st.info("还没有 AKShare 输出。")

st.divider()

st.subheader("雪球最新表")
xq_csv = latest_file("news_events_xueqiu_*.csv", ROOT / "outputs_news" / "reports")
xq_md = latest_file("news_events_xueqiu_*.md", ROOT / "outputs_news" / "reports")
xq_log = latest_file("news_events_xueqiu_*_log.csv", ROOT / "outputs_news" / "logs")
if xq_csv:
    xq_df = read_csv_any(xq_csv)
    show_metrics(xq_df, "雪球")
    st.dataframe(xq_df, use_container_width=True, height=420)
    file_download("下载雪球 CSV", xq_csv, "text/csv")
    show_xueqiu_charts(xq_df)
    show_report(xq_md, "xq_md")
    show_log(xq_log, "xq_log")
else:
    st.info("还没有雪球输出。")
