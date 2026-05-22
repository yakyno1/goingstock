# -*- coding: utf-8 -*-
from __future__ import annotations
import pandas as pd
import streamlit as st
from ui_utils import *

page_header("02｜隔夜外部变量自动包", "改用雪球逐只查询美股/ETF核心池；不再全量跑新浪美股列表。新闻仍由 AkShare 补充。")

hours = st.number_input("新闻回看小时", 1, 72, 24)
if st.button("运行隔夜外部变量自动采集", type="primary"):
    run_and_show("07_overnight_global_auto.py", ["--hours", str(hours)], timeout=1800)

st.divider()

md = latest_file("*.md", ROOT / "outputs_overnight" / "reports")
etf = latest_file("*_etf.csv", ROOT / "outputs_overnight" / "normalized")
core = latest_file("*_us_core.csv", ROOT / "outputs_overnight" / "normalized")
theme = latest_file("*_theme.csv", ROOT / "outputs_overnight" / "normalized")
news = latest_file("*_news.csv", ROOT / "outputs_overnight" / "normalized")
log = latest_file("*_log.csv", ROOT / "outputs_overnight" / "logs")

if etf:
    st.subheader("全球ETF / 风险偏好代理")
    df = read_csv_any(etf)
    st.dataframe(df, use_container_width=True)
    if "change_pct" in df.columns:
        work = df.dropna(subset=["change_pct"])
        if not work.empty:
            plot_bar_zero(work, "name_cn", "change_pct", title="全球ETF/风险偏好代理涨跌幅（%）")
        else:
            st.info("ETF 无可画数据。看接口日志判断雪球是否识别这些 symbol。")

if core:
    st.subheader("美股 / 中概 / AI链核心公司最新行情")
    df = read_csv_any(core)
    st.dataframe(df, use_container_width=True)
    if "change_pct" in df.columns:
        work = df.dropna(subset=["change_pct"])
        if not work.empty:
            plot_bar_zero(work, "name_cn", "change_pct", title="核心公司涨跌幅（%）")
        else:
            st.info("核心公司无可画数据。看接口日志，可能是雪球 symbol 需要调整。")

if theme:
    st.subheader("主题分组平均涨跌")
    df = read_csv_any(theme)
    st.dataframe(df, use_container_width=True)
    if "avg_change_pct" in df.columns:
        work = df.dropna(subset=["avg_change_pct"])
        if not work.empty:
            plot_bar_zero(work, "theme", "avg_change_pct", title="主题分组平均涨跌幅（%）")

if news:
    st.subheader("隔夜相关新闻")
    st.dataframe(read_csv_any(news), use_container_width=True, height=320)

if md:
    st.subheader("Markdown 输入包")
    show_file_card(md, "overnight_md")

if log:
    st.subheader("接口日志")
    st.dataframe(read_csv_any(log), use_container_width=True)
