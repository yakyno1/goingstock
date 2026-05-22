# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import glob
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Iterable, List, Optional, Dict, Any

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
except Exception:
    px = None
    go = None

ROOT = Path(__file__).resolve().parent


def ensure_dirs() -> None:
    for p in [
        ROOT / "outputs_combined",
        ROOT / "outputs_news" / "reports",
        ROOT / "outputs_xueqiu",
        ROOT / "outputs_xueqiu" / "history",
        ROOT / "outputs_fundflow" / "reports",
        ROOT / "outputs_fundflow" / "normalized",
        ROOT / "outputs_market_fundflow" / "reports",
        ROOT / "outputs_market_fundflow" / "normalized",
        ROOT / "outputs_overnight" / "reports",
        ROOT / "outputs_overnight" / "normalized",
    ]:
        p.mkdir(parents=True, exist_ok=True)


def _decode_subprocess_bytes(data: bytes) -> str:
    """兼容 UTF-8 / GBK / GB18030，避免 Streamlit 里 stdout/stderr 乱码。"""
    if not data:
        return ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def run_script(script: str, args: List[str], timeout: int = 1800):
    """Run one collector script with current Python. Never shell=True.

    关键修复：
    1）子进程强制 UTF-8 输出；
    2）父进程按 bytes 捕获，再智能解码；
    3）避免 Windows 下 UTF-8/GBK 混用导致中文显示成 ����。
    """
    from types import SimpleNamespace

    cmd = [sys.executable, str(ROOT / script)] + [str(a) for a in args]

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"

    cp = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=False,
        env=env,
        timeout=timeout,
    )
    return SimpleNamespace(
        args=cp.args,
        returncode=cp.returncode,
        stdout=_decode_subprocess_bytes(cp.stdout),
        stderr=_decode_subprocess_bytes(cp.stderr),
    )

def run_and_show(script: str, args: List[str], timeout: int = 1800) -> bool:
    st.code(" ".join([script] + [str(a) for a in args]), language="bash")
    with st.status("正在运行采集脚本……", expanded=True) as status:
        try:
            cp = run_script(script, args, timeout=timeout)
            if cp.stdout:
                st.text_area("STDOUT", cp.stdout[-12000:], height=260)
            if cp.stderr:
                st.text_area("STDERR", cp.stderr[-12000:], height=220)
            if cp.returncode == 0:
                status.update(label="运行完成", state="complete")
                return True
            status.update(label=f"运行失败 returncode={cp.returncode}", state="error")
            return False
        except Exception as e:
            status.update(label="运行异常", state="error")
            st.exception(e)
            return False


def latest_file(pattern: str, folder: Path | str | None = None) -> Optional[Path]:
    base = Path(folder) if folder else ROOT
    files = [Path(p) for p in glob.glob(str(base / pattern))]
    files = [p for p in files if p.exists()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def list_files(patterns: Iterable[str], folder: Path | str | None = None, limit: int = 50) -> List[Path]:
    base = Path(folder) if folder else ROOT
    files: List[Path] = []
    for pat in patterns:
        files.extend(Path(p) for p in glob.glob(str(base / pat)))
    files = [p for p in files if p.exists()]
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def read_csv_any(path: Path | str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    for enc in ["utf-8-sig", "utf-8", "gbk"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def read_text_any(path: Path | str | None) -> str:
    if not path:
        return ""
    path = Path(path)
    if not path.exists():
        return ""
    for enc in ["utf-8", "utf-8-sig", "gbk"]:
        try:
            return path.read_text(encoding=enc, errors="ignore")
        except Exception:
            pass
    return ""


def file_download(label: str, path: Path | str | None, mime: str = "text/plain") -> None:
    if not path:
        return
    path = Path(path)
    if not path.exists():
        return
    data = path.read_bytes()
    st.download_button(label, data=data, file_name=path.name, mime=mime)


def show_file_card(path: Path, key_prefix: str = "file") -> None:
    mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    st.write(f"**{path.name}**  ·  {mtime}  ·  {round(path.stat().st_size/1024, 1)} KB")
    if path.suffix.lower() == ".md":
        text = read_text_any(path)
        st.text_area("内容预览", text[:20000], height=350, key=f"{key_prefix}_{path.name}")
        file_download("下载 Markdown", path, "text/markdown")
    elif path.suffix.lower() == ".csv":
        df = read_csv_any(path)
        st.dataframe(df, use_container_width=True, height=300)
        file_download("下载 CSV", path, "text/csv")
    else:
        file_download("下载文件", path, "application/octet-stream")


def safe_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def plot_bar_zero(df: pd.DataFrame, x: str, y: str, color: Optional[str] = None, title: str = "") -> None:
    if df is None or df.empty or px is None:
        st.info("无图表数据")
        return
    work = df.copy()
    work[y] = pd.to_numeric(work[y], errors="coerce")
    fig = px.bar(work, x=x, y=y, color=color, title=title)
    fig.add_hline(y=0, line_width=1, line_dash="dash")
    fig.update_layout(yaxis_title="亿元", xaxis_title="", height=460)
    st.plotly_chart(fig, use_container_width=True)


def plot_line_zero(df: pd.DataFrame, x: str, y: str, color: Optional[str] = None, title: str = "") -> None:
    if df is None or df.empty or px is None:
        st.info("无图表数据")
        return
    work = df.copy()
    work[y] = pd.to_numeric(work[y], errors="coerce")
    fig = px.line(work, x=x, y=y, color=color, markers=True, title=title)
    fig.add_hline(y=0, line_width=1, line_dash="dash")
    fig.update_layout(yaxis_title="亿元", xaxis_title="", height=460)
    st.plotly_chart(fig, use_container_width=True)


def plot_heatmap(df: pd.DataFrame, date_col: str, name_col: str, val_col: str, title: str = "") -> None:
    if df is None or df.empty or px is None:
        st.info("无热力图数据")
        return
    work = df.copy()
    work[val_col] = pd.to_numeric(work[val_col], errors="coerce")
    pivot = work.pivot_table(index=name_col, columns=date_col, values=val_col, aggfunc="sum")
    if pivot.empty:
        st.info("无热力图数据")
        return
    # 控制大小：取绝对累计前40个板块
    order = pivot.abs().sum(axis=1).sort_values(ascending=False).head(40).index
    pivot = pivot.loc[order]
    fig = px.imshow(pivot, aspect="auto", title=title, color_continuous_midpoint=0)
    fig.update_layout(height=max(500, min(1000, len(pivot)*24)))
    st.plotly_chart(fig, use_container_width=True)


def open_folder_button(path: Path, label: str = "打开文件夹") -> None:
    if os.name == "nt":
        if st.button(label):
            try:
                os.startfile(str(path))
            except Exception as e:
                st.error(e)
    else:
        st.caption(f"目录：{path}")


def page_header(title: str, desc: str = "") -> None:
    st.set_page_config(page_title=title, layout="wide")
    st.title(title)
    if desc:
        st.caption(desc)
    ensure_dirs()
