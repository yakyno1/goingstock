# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "fundflow_cookie_clean.py"
OUT = ROOT / "outputs_fundflow_cookie_clean"
NORM = OUT / "normalized"
REPORTS = OUT / "reports"

st.set_page_config(page_title="行业概念资金流", layout="wide")
st.title("行业概念资金流")
st.caption("底层使用东财 Cookie：日期在列，前6/后6在行，统计前3/末3频次，用于观察阶段主线。")

if not SCRIPT.exists():
    st.error("缺少 fundflow_cookie_clean.py。请先安装 Cookie 版行业/概念资金流脚本。")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
with col1:
    start = st.date_input("开始日期", value=date.today() - timedelta(days=30))
with col2:
    end = st.date_input("结束日期", value=date.today())
with col3:
    board_type = st.selectbox("类型", ["both", "industry", "concept"], index=0)
with col4:
    topn = st.number_input("前N/后N", min_value=3, max_value=20, value=6, step=1)

max_boards = st.slider("最多抓取板块数", 20, 600, 120, step=20)

cookie_path = ROOT / "eastmoney_cookie.txt"
st.write("eastmoney_cookie.txt：", "已找到" if cookie_path.exists() else "未找到")

if st.button("运行行业/概念资金流采集", type="primary"):
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--start", start.strftime("%Y%m%d"),
        "--end", end.strftime("%Y%m%d"),
        "--type", board_type,
        "--topn", str(topn),
        "--max-boards", str(max_boards),
        "--rank-pz", "1000",
    ]
    env = dict(os.environ)
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    with st.spinner("正在采集东财 Cookie 行业/概念资金流..."):
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    st.code((p.stdout or "") + "\n" + (p.stderr or ""))

st.divider()

if not NORM.exists():
    st.warning("还没有输出。")
    st.stop()

pivot_files = sorted(NORM.glob("fundflow_cookie_pivot_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
freq_files = sorted(NORM.glob("fundflow_cookie_frequency_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
tb_files = sorted(NORM.glob("fundflow_cookie_top_bottom_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
all_files = sorted(NORM.glob("fundflow_cookie_all_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)

if pivot_files:
    st.subheader("横向透视表：日期在列")
    for pf in pivot_files[:4]:
        st.markdown(f"**{pf.name}**")
        df = pd.read_csv(pf, encoding="utf-8-sig")
        st.dataframe(df, use_container_width=True)
else:
    st.warning("还没有横向透视表。先运行上面的按钮。")

if freq_files:
    st.subheader("入围频次统计")
    freq = pd.read_csv(freq_files[0], encoding="utf-8-sig")
    st.dataframe(freq, use_container_width=True)

if tb_files:
    with st.expander("原始前后N长表"):
        st.dataframe(pd.read_csv(tb_files[0], encoding="utf-8-sig"), use_container_width=True)

if all_files:
    with st.expander("全量历史资金流明细"):
        st.dataframe(pd.read_csv(all_files[0], encoding="utf-8-sig"), use_container_width=True)

md_files = sorted(REPORTS.glob("fundflow_cookie_report_*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if REPORTS.exists() else []
if md_files:
    st.subheader("Markdown 报告")
    st.code(md_files[0].read_text(encoding="utf-8", errors="ignore")[:8000])
