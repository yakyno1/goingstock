# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from pandas.errors import EmptyDataError

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:
    go = None
    make_subplots = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "08_eastmoney_market_fundflow_cookie.py"
NORM = ROOT / "outputs_market_fundflow_cookie" / "normalized"
REPORTS = ROOT / "outputs_market_fundflow_cookie" / "reports"

st.set_page_config(page_title="Market Fundflow", layout="wide")
st.title("大盘资金流")
st.caption("东财 Cookie 版：调用 08_eastmoney_market_fundflow_cookie.py，不调用旧 AkShare stock_market_fund_flow。")


def read_csv_safe(path: Path) -> pd.DataFrame:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame()
        return pd.read_csv(path, encoding="utf-8-sig")
    except EmptyDataError:
        return pd.DataFrame()
    except Exception:
        try:
            return pd.read_csv(path, encoding="gb18030")
        except Exception:
            return pd.DataFrame()


def get_files() -> list[Path]:
    if not NORM.exists():
        return []
    files = sorted(NORM.glob("market_fundflow_cookie_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in files if "all_" not in p.name.lower() and "log" not in p.name.lower() and p.stat().st_size > 0]


def clean_empty_csvs() -> int:
    if not NORM.exists():
        return 0
    n = 0
    for p in NORM.glob("*.csv"):
        try:
            if p.stat().st_size == 0:
                p.unlink()
                n += 1
        except Exception:
            pass
    return n


if not SCRIPT.exists():
    st.error("缺少核心脚本：08_eastmoney_market_fundflow_cookie.py")
    st.stop()

col1, col2 = st.columns(2)
with col1:
    start = st.date_input("开始日期", value=date.today() - timedelta(days=365 * 3))
with col2:
    end = st.date_input("结束日期", value=date.today())

cookie_path = ROOT / "eastmoney_cookie.txt"
st.write("eastmoney_cookie.txt：", "已找到" if cookie_path.exists() else "未找到")

c1, c2 = st.columns([1, 1])
with c1:
    run_clicked = st.button("运行大盘资金流采集", type="primary")
with c2:
    if st.button("清理空 CSV"):
        n = clean_empty_csvs()
        st.success(f"已清理空 CSV：{n} 个")
        st.rerun()

if run_clicked:
    cmd = [sys.executable, str(SCRIPT), "--start", start.strftime("%Y%m%d"), "--end", end.strftime("%Y%m%d")]
    env = dict(os.environ)
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    with st.spinner("正在运行东财 Cookie 大盘资金流采集..."):
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    st.subheader("运行输出")
    st.code((p.stdout or "") + "\n" + (p.stderr or ""))

st.divider()

files = get_files()
if not files:
    st.warning("还没有可用的大盘资金流 CSV。先点击上面的采集按钮。")
    st.stop()

selected = st.selectbox("选择输出 CSV", files, format_func=lambda p: p.name)
df = read_csv_safe(selected)

if df.empty:
    st.warning("当前 CSV 是空表或坏表。请重新采集。")
    st.stop()

st.subheader("原始数据")
st.dataframe(df, use_container_width=True)

if "date" not in df.columns:
    st.warning("CSV 缺少 date 字段，无法画图。")
    st.stop()

for c in df.columns:
    if c != "date":
        df[c] = pd.to_numeric(df[c], errors="coerce")

dt = pd.to_datetime(df["date"], errors="coerce")
if dt.notna().any():
    st.info(f"当前文件实际覆盖日期：{dt.min().date()} 至 {dt.max().date()}，共 {len(df)} 行。东财该接口历史长度可能有限。")

if go is not None and make_subplots is not None and all(c in df.columns for c in ["main_net_inflow_yi", "sh_change_pct", "sz_change_pct"]):
    st.subheader("主力资金流 vs 指数涨跌幅")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=df["date"], y=df["main_net_inflow_yi"], name="主力净流入（亿元）", opacity=0.65), secondary_y=False)
    fig.add_trace(go.Scatter(x=df["date"], y=df["sh_change_pct"], mode="lines+markers", name="上证涨跌幅（%）"), secondary_y=True)
    fig.add_trace(go.Scatter(x=df["date"], y=df["sz_change_pct"], mode="lines+markers", name="深成指涨跌幅（%）"), secondary_y=True)
    fig.add_hline(y=0, line_width=1, line_dash="dot", secondary_y=False)
    fig.update_yaxes(title_text="资金流（亿元）", secondary_y=False)
    fig.update_yaxes(title_text="指数涨跌幅（%）", secondary_y=True)
    fig.update_layout(height=520)
    st.plotly_chart(fig, use_container_width=True)

md_files = sorted(REPORTS.glob("market_fundflow_cookie_*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if REPORTS.exists() else []
if md_files:
    st.subheader("Markdown 报告")
    st.code(md_files[0].read_text(encoding="utf-8", errors="ignore")[:8000])
