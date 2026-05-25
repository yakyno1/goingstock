
# -*- coding: utf-8 -*-
"""
fundflow_cookie_clean.py

彻底绕开旧 AkShare 资金流函数：
- 不调用 stock_sector_fund_flow_rank
- 不调用 stock_sector_fund_flow_hist
- 不调用 stock_concept_fund_flow_hist
- 只用东方财富 Cookie + weblogin 当前排名 + push2his 历史 daykline

输出：
outputs_fundflow_cookie_clean/
  normalized/
    board_fundflow_all_*.csv
    board_fundflow_top_bottom_*.csv
    board_fundflow_pivot_*.csv
    board_fundflow_frequency_*.csv
  reports/
    board_fundflow_report_*.md
  logs/
    board_fundflow_log_*.csv
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
COOKIE_FILE = ROOT / "eastmoney_cookie.txt"
OUT = ROOT / "outputs_fundflow_cookie_clean"
RAW_DIR = OUT / "raw"
NORM_DIR = OUT / "normalized"
REPORT_DIR = OUT / "reports"
LOG_DIR = OUT / "logs"
for d in [RAW_DIR, NORM_DIR, REPORT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def clear_proxy_env() -> None:
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_cookie() -> str:
    if not COOKIE_FILE.exists():
        return ""
    text = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    if text.startswith("EASTMONEY_COOKIE="):
        text = text.split("=", 1)[1].strip()
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return " ".join(lines).strip().strip('"').strip("'")


def parse_json_or_jsonp(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    m = re.search(r"^[\w$]+\((.*)\)\s*;?$", text, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError(f"返回不是 JSON/JSONP: {text[:300]}")


def make_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def get_json_safe(session: requests.Session, url: str, params: Dict[str, Any], referer: str, timeout: int = 20) -> Tuple[Optional[Dict[str, Any]], str, int, str]:
    headers = {"Referer": referer}
    try:
        r = session.get(url, params=params, headers=headers, timeout=timeout)
        raw = r.text or ""
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {raw[:300]}", r.status_code, raw
        try:
            return parse_json_or_jsonp(raw), "", r.status_code, raw
        except Exception as e:
            return None, f"JSON_PARSE_ERROR: {repr(e)}; head={raw[:300]}", r.status_code, raw
    except Exception as e:
        return None, f"{type(e).__name__}: {repr(e)}", 0, ""


def fetch_rank(session: requests.Session, board_type: str, pz: int) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    logs: List[Dict[str, Any]] = []

    if board_type == "industry":
        fs = "m:90+t:2"
        referer = "https://data.eastmoney.com/bkzj/hy.html"
    elif board_type == "concept":
        fs = "m:90+t:3"
        referer = "https://data.eastmoney.com/bkzj/gn.html"
    else:
        raise ValueError(board_type)

    url = "https://push2.eastmoney.com/weblogin/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": str(pz),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": fs,
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13",
        "_": str(int(time.time() * 1000)),
    }

    js, err, status, raw = get_json_safe(session, url, params, referer)
    raw_path = RAW_DIR / f"rank_{board_type}_{now_tag()}.txt"
    raw_path.write_text(raw or "", encoding="utf-8", errors="ignore")

    if err:
        logs.append({"stage": "rank", "board_type": board_type, "api": "eastmoney_weblogin_rank", "status": "ERROR", "rows": 0, "error": err, "board_name": ""})
        return pd.DataFrame(), logs

    data = ((js or {}).get("data") or {}).get("diff") or []
    df = pd.DataFrame(data)
    if df.empty:
        logs.append({"stage": "rank", "board_type": board_type, "api": "eastmoney_weblogin_rank", "status": "ERROR", "rows": 0, "error": "empty diff", "board_name": ""})
        return df, logs

    df["board_type"] = board_type
    rename = {
        "f12": "board_code",
        "f14": "board_name",
        "f2": "close",
        "f3": "change_pct",
        "f62": "main_net_inflow",
        "f66": "super_big_net_inflow",
        "f72": "big_net_inflow",
        "f78": "medium_net_inflow",
        "f84": "small_net_inflow",
        "f184": "main_net_ratio",
        "f204": "max_net_inflow_stock",
        "f205": "max_net_inflow_stock_code",
        "f124": "timestamp",
    }
    df = df.rename(columns=rename)
    keep = [c for c in ["board_type","board_code","board_name","close","change_pct","main_net_inflow","super_big_net_inflow","big_net_inflow","medium_net_inflow","small_net_inflow","main_net_ratio","max_net_inflow_stock","max_net_inflow_stock_code","timestamp"] if c in df.columns]
    df = df[keep].copy()

    for c in ["close","change_pct","main_net_inflow","super_big_net_inflow","big_net_inflow","medium_net_inflow","small_net_inflow","main_net_ratio"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    logs.append({"stage": "rank", "board_type": board_type, "api": "eastmoney_weblogin_rank", "status": "OK", "rows": len(df), "error": "", "board_name": ""})
    return df, logs


def fetch_board_history(
    session: requests.Session,
    board_type: str,
    board_code: str,
    board_name: str,
    start: str = "",
    end: str = "",
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    urls = [
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        "https://push2his.eastmoney.com/weblogin/api/qt/stock/fflow/daykline/get",
    ]
    params = {
        "lmt": "100000",
        "klt": "101",
        "secid": f"90.{board_code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": str(int(time.time() * 1000)),
    }
    # Some accounts/environments return only recent rows without explicit window.
    # Pass beg/end to request the full target range when supported.
    s = str(start).replace("-", "").strip()
    e = str(end).replace("-", "").strip()
    if s:
        params["beg"] = s
    if e:
        params["end"] = e
    referer = "https://data.eastmoney.com/bkzj/hy.html" if board_type == "industry" else "https://data.eastmoney.com/bkzj/gn.html"

    param_variants: List[Dict[str, Any]] = []
    # Variant A: explicit range (best when API honors beg/end)
    param_variants.append(dict(params))
    # Variant B: without range, often returns the latest ~120 days more stably
    p2 = dict(params)
    p2.pop("beg", None)
    p2.pop("end", None)
    p2["lmt"] = "120"
    param_variants.append(p2)

    last_err = ""
    for url in urls:
        for pv in param_variants:
            for attempt in range(2):
                js, err, status, raw = get_json_safe(session, url, pv, referer)
                if err:
                    last_err = err
                    # transient failures: short retry
                    if attempt == 0 and ("ReadTimeout" in err or "ConnectionError" in err or "HTTP 5" in err):
                        time.sleep(0.25)
                        continue
                    break

                data = (js or {}).get("data") or {}
                klines = data.get("klines") or []
                if not klines:
                    last_err = "empty klines"
                    break

                rows = [str(x).split(",") for x in klines]
                df = pd.DataFrame(rows)
                cols = [
                    "date",
                    "main_net_inflow",
                    "small_net_inflow",
                    "medium_net_inflow",
                    "big_net_inflow",
                    "super_big_net_inflow",
                    "main_net_ratio",
                    "small_net_ratio",
                    "medium_net_ratio",
                    "big_net_ratio",
                    "super_big_net_ratio",
                    "close",
                    "change_pct",
                ]
                if df.shape[1] >= len(cols):
                    df = df.iloc[:, :len(cols)]
                    df.columns = cols
                else:
                    last_err = f"字段数异常 {df.shape[1]}"
                    break

                for c in df.columns:
                    if c != "date":
                        df[c] = pd.to_numeric(df[c], errors="coerce")

                df["board_type"] = board_type
                df["board_code"] = board_code
                df["board_name"] = board_name

                for c in ["main_net_inflow","small_net_inflow","medium_net_inflow","big_net_inflow","super_big_net_inflow"]:
                    df[c + "_yi"] = df[c] / 1e8

                return df, {"stage": "hist", "board_type": board_type, "api": "eastmoney_board_history", "status": "OK", "rows": len(df), "error": "", "board_name": board_name}

    return pd.DataFrame(), {"stage": "hist", "board_type": board_type, "api": "eastmoney_board_history", "status": "ERROR", "rows": 0, "error": last_err, "board_name": board_name}


def filter_dates(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if df.empty:
        return df
    s = str(start).replace("-", "")
    e = str(end).replace("-", "")
    d = df.copy()
    d["_date_key"] = d["date"].astype(str).str.replace("-", "", regex=False)
    d = d[(d["_date_key"] >= s) & (d["_date_key"] <= e)].drop(columns=["_date_key"])
    return d.reset_index(drop=True)


def make_top_bottom(all_df: pd.DataFrame, topn: int) -> pd.DataFrame:
    cols = [
        "date", "board_type", "rank_type", "rank",
        "board_name", "board_code", "net_inflow_yi",
        "change_pct", "close", "source_api",
    ]
    rows = []
    if all_df.empty:
        return pd.DataFrame(columns=cols)
    for (bt, d), g in all_df.groupby(["board_type", "date"], dropna=False):
        g = g.dropna(subset=["main_net_inflow_yi"]).copy()
        if g.empty:
            continue
        g = g.sort_values("main_net_inflow_yi", ascending=False).drop_duplicates("board_name", keep="first")
        # Always take top/bottom N by value. Requiring >0/<0 causes sparse days.
        top = g.sort_values("main_net_inflow_yi", ascending=False).head(topn)
        bottom = g.sort_values("main_net_inflow_yi", ascending=True).head(topn)

        for i, (_, r) in enumerate(top.iterrows(), 1):
            rows.append({
                "date": d, "board_type": bt, "rank_type": "top_inflow", "rank": i,
                "board_name": r["board_name"], "board_code": r["board_code"],
                "net_inflow_yi": r["main_net_inflow_yi"],
                "change_pct": r.get("change_pct"), "close": r.get("close"),
                "source_api": "eastmoney_cookie_direct"
            })
        for i, (_, r) in enumerate(bottom.iterrows(), 1):
            rows.append({
                "date": d, "board_type": bt, "rank_type": "bottom_outflow", "rank": i,
                "board_name": r["board_name"], "board_code": r["board_code"],
                "net_inflow_yi": r["main_net_inflow_yi"],
                "change_pct": r.get("change_pct"), "close": r.get("close"),
                "source_api": "eastmoney_cookie_direct"
            })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def cell_text(name: str, value: float) -> str:
    return f"{name}（{'+' if value >= 0 else ''}{value:.2f}亿）"


def make_pivot(tb: pd.DataFrame, board_type: str, topn: int) -> pd.DataFrame:
    if tb.empty or "board_type" not in tb.columns:
        return pd.DataFrame()
    part = tb[tb["board_type"] == board_type].copy()
    if part.empty:
        return pd.DataFrame()
    dates = sorted(part["date"].unique().tolist())
    rows = []
    for rank_type, label in [("top_inflow", "前"), ("bottom_outflow", "后")]:
        for r in range(1, topn + 1):
            row = {"排名": f"{label}{r}"}
            for d in dates:
                hit = part[(part["date"] == d) & (part["rank_type"] == rank_type) & (part["rank"] == r)]
                row[d] = "" if hit.empty else cell_text(str(hit.iloc[0]["board_name"]), float(hit.iloc[0]["net_inflow_yi"]))
            rows.append(row)
    return pd.DataFrame(rows)


def make_frequency(tb: pd.DataFrame, topn: int) -> pd.DataFrame:
    if tb.empty or "board_type" not in tb.columns:
        return pd.DataFrame()
    rows = []
    for (bt, name), g in tb.groupby(["board_type", "board_name"], dropna=False):
        top3 = g[(g["rank_type"] == "top_inflow") & (g["rank"] <= 3)]
        bot3 = g[(g["rank_type"] == "bottom_outflow") & (g["rank"] <= 3)]
        topn_g = g[(g["rank_type"] == "top_inflow") & (g["rank"] <= topn)]
        botn_g = g[(g["rank_type"] == "bottom_outflow") & (g["rank"] <= topn)]
        rows.append({
            "board_type": bt,
            "board_name": name,
            "前3频次": len(top3),
            "末3频次": len(bot3),
            f"前{topn}频次": len(topn_g),
            f"末{topn}频次": len(botn_g),
            "前3日期": ",".join(sorted(top3["date"].astype(str).unique().tolist())),
            "末3日期": ",".join(sorted(bot3["date"].astype(str).unique().tolist())),
            "净强势频次": len(top3) - len(bot3),
            "区间净流入合计_亿": round(float(g["net_inflow_yi"].sum()), 4),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["净强势频次", "前3频次", "区间净流入合计_亿"], ascending=[False, False, False]).reset_index(drop=True)


def df_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "缺数据"
    try:
        return df.to_markdown(index=False)
    except Exception:
        return df.to_csv(index=False)


def build_report(start: str, end: str, pivots: Dict[str, pd.DataFrame], freq: pd.DataFrame, logs: pd.DataFrame, topn: int) -> str:
    lines = []
    lines.append("# 行业/概念历史资金流横向输入包")
    lines.append("")
    lines.append(f"区间：{start} 至 {end}")
    lines.append("")
    lines.append("单位：亿元；正数净流入，负数净流出。")
    lines.append("")
    lines.append("说明：每一列是一个交易日；行是前N/后N。最后频次统计用于识别阶段主线。")
    lines.append("")
    for bt, table in pivots.items():
        lines.append(f"## {bt} 每日前{topn}/后{topn}")
        lines.append("")
        lines.append(df_md(table))
        lines.append("")
    lines.append("## 入围频次统计")
    lines.append("")
    lines.append(df_md(freq))
    lines.append("")
    lines.append("## 接口日志")
    lines.append("")
    lines.append(df_md(logs))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260422")
    parser.add_argument("--end", default="20260522")
    parser.add_argument("--type", choices=["industry","concept","both"], default="both")
    parser.add_argument("--topn", type=int, default=6)
    parser.add_argument("--max-boards", type=int, default=120)
    parser.add_argument("--rank-pz", type=int, default=1000)
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    clear_proxy_env()
    cookie = load_cookie()
    session = make_session(cookie)

    tag = now_tag()
    all_parts = []
    log_rows = []

    board_types = ["industry","concept"] if args.type == "both" else [args.type]

    for bt in board_types:
        rank_df, logs = fetch_rank(session, bt, args.rank_pz)
        log_rows.extend(logs)
        if rank_df.empty:
            continue

        rank_path = RAW_DIR / f"rank_{bt}_{tag}.csv"
        rank_df.to_csv(rank_path, index=False, encoding="utf-8-sig")

        boards = rank_df.dropna(subset=["board_code","board_name"]).head(args.max_boards)
        total = len(boards)
        print(f"[{bt}] boards={total}")

        for i, (_, r) in enumerate(boards.iterrows(), 1):
            code = str(r["board_code"])
            name = str(r["board_name"])
            print(f"[{bt}] {i}/{total} {name} {code}")
            hist, log = fetch_board_history(session, bt, code, name, start=args.start, end=args.end)
            log_rows.append(log)
            if not hist.empty:
                hist2 = filter_dates(hist, args.start, args.end)
                if not hist2.empty:
                    all_parts.append(hist2)
            time.sleep(args.sleep)

    logs_df = pd.DataFrame(log_rows)
    all_df = pd.concat(all_parts, ignore_index=True) if all_parts else pd.DataFrame()

    all_csv = NORM_DIR / f"fundflow_cookie_all_{args.start}_{args.end}_{tag}.csv"
    all_df.to_csv(all_csv, index=False, encoding="utf-8-sig")

    tb = make_top_bottom(all_df, args.topn)
    tb_csv = NORM_DIR / f"fundflow_cookie_top_bottom_{args.start}_{args.end}_{tag}.csv"
    tb.to_csv(tb_csv, index=False, encoding="utf-8-sig")

    pivots = {}
    for bt in board_types:
        piv = make_pivot(tb, bt, args.topn)
        pivots[bt] = piv
        piv.to_csv(NORM_DIR / f"fundflow_cookie_pivot_{bt}_{args.start}_{args.end}_{tag}.csv", index=False, encoding="utf-8-sig")

    freq = make_frequency(tb, args.topn)
    freq_csv = NORM_DIR / f"fundflow_cookie_frequency_{args.start}_{args.end}_{tag}.csv"
    freq.to_csv(freq_csv, index=False, encoding="utf-8-sig")

    log_csv = LOG_DIR / f"fundflow_cookie_log_{args.start}_{args.end}_{tag}.csv"
    logs_df.to_csv(log_csv, index=False, encoding="utf-8-sig")

    md = REPORT_DIR / f"fundflow_cookie_report_{args.start}_{args.end}_{tag}.md"
    md.write_text(build_report(args.start, args.end, pivots, freq, logs_df, args.topn), encoding="utf-8")

    print("[输出完成]")
    print("all_csv:", all_csv)
    print("top_bottom_csv:", tb_csv)
    print("frequency_csv:", freq_csv)
    print("log:", log_csv)
    print("md:", md)
    if freq.empty:
        print("[频次] 缺数据")
    else:
        print(freq.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
