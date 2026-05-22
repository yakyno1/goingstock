# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "08_xueqiu_live_news_collect.py"
OUT = ROOT / "outputs_xueqiu_news"

st.set_page_config(page_title="08 雪球快讯采集", layout="wide")
st.title("08｜雪球快讯 / 盘前快讯采集")
st.caption("优先用雪球快讯/帖子接口，适合替代慢速全量新闻源。输出原始输入包，不下交易结论。")

col1, col2, col3 = st.columns(3)
with col1:
    mode = st.selectbox("模式", ["all", "livenews", "search", "timeline"], index=0)
with col2:
    hours = st.number_input("回看小时", 1, 168, 24, 1)
with col3:
    max_rows = st.number_input("最多输出", 20, 1000, 200, 20)

queries = st.text_input("搜索关键词", "7X24快讯,盘前快讯,隔夜,美股,英伟达,财报,长鑫,H200,FSD,AI算力,半导体")
keywords = st.text_input("输出过滤关键词（可空）", "")
user_id = st.text_input("可选：雪球用户ID（如果你从URL拿到7X24快讯的user_id可填）", "")

if st.button("运行雪球快讯采集", type="primary"):
    if not SCRIPT.exists():
        st.error(f"缺少脚本：{SCRIPT}")
    else:
        cmd = [sys.executable, str(SCRIPT), "--mode", mode, "--hours", str(hours), "--max", str(max_rows), "--queries", queries]
        if keywords.strip():
            cmd.extend(["--keywords", keywords.strip()])
        if user_id.strip():
            cmd.extend(["--user-id", user_id.strip()])
        env = os.environ.copy()
        env.update({
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "*",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
        })
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True)
        def dec(b: bytes) -> str:
            for enc in ["utf-8", "gb18030", "gbk"]:
                try:
                    return b.decode(enc)
                except Exception:
                    pass
            return b.decode("utf-8", errors="replace")
        st.write("命令：", " ".join(cmd))
        if p.returncode == 0:
            st.success("运行完成")
        else:
            st.error(f"运行失败 returncode={p.returncode}")
        with st.expander("STDOUT", expanded=True):
            st.code(dec(p.stdout))
        if p.stderr:
            with st.expander("STDERR", expanded=True):
                st.code(dec(p.stderr))

st.divider()
st.subheader("最近输出")

norm_dir = OUT / "normalized"
report_dir = OUT / "reports"
log_dir = OUT / "logs"

csv_files = sorted(norm_dir.glob("xueqiu_live_news_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True) if norm_dir.exists() else []
md_files = sorted(report_dir.glob("xueqiu_live_news_packet_*.md"), key=lambda x: x.stat().st_mtime, reverse=True) if report_dir.exists() else []
log_files = sorted(log_dir.glob("xueqiu_live_news_log_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True) if log_dir.exists() else []

if csv_files:
    st.caption(f"最新CSV：{csv_files[0]}")
    try:
        df = pd.read_csv(csv_files[0], encoding="utf-8-sig")
        st.dataframe(df, use_container_width=True)
        if not df.empty and "importance" in df.columns:
            st.bar_chart(df.head(30).set_index("title")["importance"])
    except Exception as e:
        st.error(f"读取CSV失败：{e}")
else:
    st.info("暂无CSV输出")

if md_files:
    st.caption(f"最新Markdown：{md_files[0]}")
    with st.expander("Markdown 输入包", expanded=False):
        st.markdown(md_files[0].read_text(encoding="utf-8", errors="ignore"))

if log_files:
    st.caption(f"最新日志：{log_files[0]}")
    try:
        st.dataframe(pd.read_csv(log_files[0], encoding="utf-8-sig"), use_container_width=True)
    except Exception as e:
        st.error(f"读取日志失败：{e}")
