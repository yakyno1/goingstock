# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
COOKIE_FILE = ROOT / "eastmoney_cookie.txt"

OUT = ROOT / "outputs_market_fundflow_cookie"
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


def get_json_safe(session: requests.Session, url: str, params: Dict[str, Any], referer: str, timeout: int = 20) -> Tuple[Optional[Dict[str, Any]], str, str]:
    headers = {"Referer": referer}
    try:
        r = session.get(url, params=params, headers=headers, timeout=timeout)
        raw = r.text or ""
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {raw[:300]}", raw
        try:
            return parse_json_or_jsonp(raw), "", raw
        except Exception as e:
            return None, f"JSON_PARSE_ERROR: {repr(e)}; head={raw[:300]}", raw
    except Exception as e:
        return None, f"{type(e).__name__}: {repr(e)}", ""


def fetch_market_fundflow() -> tuple[pd.DataFrame, pd.DataFrame]:
    clear_proxy_env()
    cookie = load_cookie()
    session = make_session(cookie)

    params = {
        "lmt": "100000",
        "klt": "101",
        "secid": "1.000001",
        "secid2": "0.399001",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": str(int(time.time() * 1000)),
    }

    urls = [
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        "https://push2his.eastmoney.com/weblogin/api/qt/stock/fflow/daykline/get",
    ]

    referer = "https://data.eastmoney.com/zjlx/dpzjlx.html"
    logs = []
    last_error = ""

    for url in urls:
        js, err, raw = get_json_safe(session, url, params, referer)
        raw_path = RAW_DIR / f"market_fundflow_raw_{now_tag()}.txt"
        raw_path.write_text(raw or "", encoding="utf-8", errors="ignore")

        if err:
            logs.append({"api": "eastmoney_market_daykline_cookie", "url": url, "status": "ERROR", "rows": 0, "error": err})
            last_error = err
            continue

        data = (js or {}).get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            logs.append({"api": "eastmoney_market_daykline_cookie", "url": url, "status": "ERROR", "rows": 0, "error": f"empty klines; data={str(data)[:300]}"})
            last_error = "empty klines"
            continue

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
            "sh_close",
            "sh_change_pct",
            "sz_close",
            "sz_change_pct",
        ]

        if df.shape[1] >= len(cols):
            df = df.iloc[:, :len(cols)]
            df.columns = cols
        else:
            logs.append({"api": "eastmoney_market_daykline_cookie", "url": url, "status": "ERROR", "rows": len(df), "error": f"字段数量异常: {df.shape[1]}"})
            last_error = f"字段数量异常: {df.shape[1]}"
            continue

        for c in df.columns:
            if c != "date":
                df[c] = pd.to_numeric(df[c], errors="coerce")

        for c in ["main_net_inflow", "small_net_inflow", "medium_net_inflow", "big_net_inflow", "super_big_net_inflow"]:
            df[c + "_yi"] = df[c] / 1e8

        logs.append({"api": "eastmoney_market_daykline_cookie", "url": url, "status": "OK", "rows": len(df), "error": ""})
        return df, pd.DataFrame(logs)

    return pd.DataFrame(), pd.DataFrame(logs or [{"api": "eastmoney_market_daykline_cookie", "url": "", "status": "ERROR", "rows": 0, "error": last_error}])


def filter_dates(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df
    s = str(start).replace("-", "")
    e = str(end).replace("-", "")
    out = df.copy()
    out["_k"] = out["date"].astype(str).str.replace("-", "", regex=False)
    out = out[(out["_k"] >= s) & (out["_k"] <= e)].drop(columns=["_k"])
    return out.reset_index(drop=True)


def build_report(df: pd.DataFrame, logs: pd.DataFrame, start: str, end: str) -> str:
    lines = []
    lines.append("# 大盘历史资金流输入包（东财 Cookie 版）")
    lines.append("")
    lines.append(f"区间：{start} 至 {end}")
    lines.append("")
    lines.append("单位：亿元；正数净流入，负数净流出。")
    lines.append("")
    lines.append("## 1. 大盘资金流历史表")
    lines.append("")
    if df.empty:
        lines.append("缺数据")
    else:
        show_cols = ["date", "main_net_inflow_yi", "super_big_net_inflow_yi", "big_net_inflow_yi", "medium_net_inflow_yi", "small_net_inflow_yi", "main_net_ratio", "sh_close", "sh_change_pct", "sz_close", "sz_change_pct"]
        show_cols = [c for c in show_cols if c in df.columns]
        show = df[show_cols].copy()
        for c in show.columns:
            if c != "date":
                show[c] = pd.to_numeric(show[c], errors="coerce").round(4)
        lines.append(show.to_markdown(index=False))
    lines.append("")
    lines.append("## 2. 接口日志")
    lines.append("")
    lines.append(logs.to_markdown(index=False) if not logs.empty else "缺日志")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260422")
    parser.add_argument("--end", default="20260522")
    args = parser.parse_args()

    tag = now_tag()
    df, logs = fetch_market_fundflow()
    df2 = filter_dates(df, args.start, args.end)

    all_csv = NORM_DIR / f"market_fundflow_cookie_all_{args.start}_{args.end}_{tag}.csv"
    df.to_csv(all_csv, index=False, encoding="utf-8-sig")

    filtered_csv = NORM_DIR / f"market_fundflow_cookie_{args.start}_{args.end}_{tag}.csv"
    df2.to_csv(filtered_csv, index=False, encoding="utf-8-sig")

    log_csv = LOG_DIR / f"market_fundflow_cookie_{args.start}_{args.end}_{tag}_log.csv"
    logs.to_csv(log_csv, index=False, encoding="utf-8-sig")

    md = REPORT_DIR / f"market_fundflow_cookie_{args.start}_{args.end}_{tag}.md"
    md.write_text(build_report(df2, logs, args.start, args.end), encoding="utf-8")

    print("[输出完成]")
    print("all_csv:", all_csv)
    print("filtered_csv:", filtered_csv)
    print("log:", log_csv)
    print("md:", md)

    if df2.empty:
        print("[预览] 缺数据。请查看 log。")
    else:
        print(df2.tail().to_string(index=False))


if __name__ == "__main__":
    main()
