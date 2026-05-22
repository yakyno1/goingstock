# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import pandas as pd
import streamlit as st
from ui_utils import ROOT, ensure_dirs, list_files, read_csv_any, read_text_any, open_folder_button, file_download

st.set_page_config(page_title="交易前线控制台", layout="wide")
ensure_dirs()
st.title("交易前线控制台｜Streamlit 完整版 v1")
st.caption("命令行采集为底座；Streamlit 只负责可视化、运行按钮、报告下载和合并。")

cols = st.columns(4)
watch = ROOT / "watchlist_xueqiu.csv"
cookie = ROOT / "xueqiu_cookie.txt"
news_reports = list_files(["*.md"], ROOT / "outputs_news" / "reports", 5)
xq_reports = list_files(["xueqiu_packet_*.md"], ROOT / "outputs_xueqiu", 5)
fund_reports = list_files(["*.md"], ROOT / "outputs_fundflow" / "reports", 5)
market_reports = list_files(["*.md"], ROOT / "outputs_market_fundflow" / "reports", 5)
with cols[0]: st.metric("雪球自选表", "存在" if watch.exists() else "缺失")
with cols[1]: st.metric("雪球Cookie", "已填" if cookie.exists() and "xq_a_token=" in read_text_any(cookie) else "未确认")
with cols[2]: st.metric("新闻报告", len(news_reports))
with cols[3]: st.metric("雪球盘中包", len(xq_reports))

st.subheader("系统模块")
st.write("左侧选择页面。建议顺序：③雪球盘中 → ①新闻事件 → ②外部变量 → ④/⑤资金流 → 报告中心。")

st.subheader("最近文件")
all_files = list_files([
    "outputs_news/reports/*.md", "outputs_xueqiu/*.md", "outputs_fundflow/reports/*.md", "outputs_market_fundflow/reports/*.md", "outputs_overnight/reports/*.md"
], ROOT, 12)
if all_files:
    df = pd.DataFrame({
        "文件": [str(p.relative_to(ROOT)) for p in all_files],
        "修改时间": [datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") for p in all_files],
        "大小KB": [round(p.stat().st_size/1024,1) for p in all_files],
    })
    st.dataframe(df, use_container_width=True)
else:
    st.info("还没有输出报告。")

st.subheader("输出目录")
for name, path in [
    ("新闻事件", ROOT/"outputs_news"/"reports"),
    ("雪球盘中", ROOT/"outputs_xueqiu"),
    ("行业概念资金流", ROOT/"outputs_fundflow"/"reports"),
    ("大盘资金流", ROOT/"outputs_market_fundflow"/"reports"),
    ("隔夜外部变量", ROOT/"outputs_overnight"/"reports"),
]:
    st.write(f"**{name}**：`{path}`")
