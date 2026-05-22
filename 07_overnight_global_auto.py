# -*- coding: utf-8 -*-
"""
07_overnight_global_auto.py
v5：隔夜外部变量自动包，改用雪球逐只查询美股/ETF，不再全量跑新浪美股列表。

定位：
- 快：固定核心池逐只/批量查询，不扫全市场。
- 稳：读取 xueqiu_cookie.txt；先访问雪球首页；batch失败后逐只降级。
- 原始输入包：只输出行情/新闻/映射，不给交易结论。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:
    ak = None

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs_overnight"
RAW = OUT / "raw"
NORM = OUT / "normalized"
REP = OUT / "reports"
LOG = OUT / "logs"
for d in [RAW, NORM, REP, LOG]:
    d.mkdir(parents=True, exist_ok=True)

XUEQIU_HOME = "https://xueqiu.com/"
XUEQIU_BASE = "https://stock.xueqiu.com"
COOKIE_FILE = ROOT / "xueqiu_cookie.txt"

# 用 ETF 代理指数，不再抓全量新浪美股。
ETF_TARGETS = [
    {"symbol": "SPY", "name_cn": "标普500ETF", "theme": "美股三大指数/风险偏好", "a_h_mapping": "全球risk-on/risk-off"},
    {"symbol": "QQQ", "name_cn": "纳斯达克100ETF", "theme": "美股科技/AI风险偏好", "a_h_mapping": "AI算力/半导体/港股科技"},
    {"symbol": "DIA", "name_cn": "道琼斯ETF", "theme": "美股蓝筹", "a_h_mapping": "全球风险偏好"},
    {"symbol": "SOXX", "name_cn": "费城半导体ETF", "theme": "半导体指数代理", "a_h_mapping": "半导体/存储/设备/AI芯片"},
    {"symbol": "SMH", "name_cn": "VanEck半导体ETF", "theme": "半导体指数代理", "a_h_mapping": "半导体/存储/设备/AI芯片"},
    {"symbol": "KWEB", "name_cn": "中概互联网ETF", "theme": "中概/港股映射", "a_h_mapping": "阿里/腾讯/美团/京东/B站/港股科技"},
    {"symbol": "FXI", "name_cn": "中国大盘ETF", "theme": "中概/港股映射", "a_h_mapping": "港股/中概/A股风险偏好"},
]

CORE_TARGETS = [
    {"symbol": "NVDA", "name_cn": "英伟达", "theme": "AI芯片/GPU", "a_h_mapping": "工业富联/中际旭创/新易盛/天孚通信/沪电股份/胜宏科技"},
    {"symbol": "AMD", "name_cn": "AMD", "theme": "AI芯片/GPU", "a_h_mapping": "寒武纪/海光信息/AI芯片链"},
    {"symbol": "AVGO", "name_cn": "博通", "theme": "ASIC/交换机/半导体", "a_h_mapping": "沪电股份/胜宏科技/通信设备/交换机链"},
    {"symbol": "MU", "name_cn": "美光", "theme": "存储/HBM", "a_h_mapping": "澜起科技/江波龙/兆易创新/佰维存储"},
    {"symbol": "TSM", "name_cn": "台积电", "theme": "晶圆制造", "a_h_mapping": "中芯国际/半导体设备/材料"},
    {"symbol": "ASML", "name_cn": "阿斯麦", "theme": "半导体设备", "a_h_mapping": "北方华创/中微公司/拓荆科技/华海清科"},
    {"symbol": "ARM", "name_cn": "ARM", "theme": "CPU/IP", "a_h_mapping": "海光信息/龙芯中科/国产CPU"},
    {"symbol": "MRVL", "name_cn": "Marvell", "theme": "交换机/光通信/ASIC", "a_h_mapping": "光模块/CPO/交换机链"},
    {"symbol": "SMCI", "name_cn": "超微电脑", "theme": "AI服务器", "a_h_mapping": "工业富联/中科曙光/浪潮信息/AI服务器"},
    {"symbol": "DELL", "name_cn": "戴尔", "theme": "AI服务器", "a_h_mapping": "工业富联/服务器OEM/液冷"},
    {"symbol": "ANET", "name_cn": "Arista", "theme": "AI交换机/网络", "a_h_mapping": "沪电股份/胜宏科技/交换机/高速PCB"},
    {"symbol": "VRT", "name_cn": "Vertiv", "theme": "数据中心电力/液冷", "a_h_mapping": "液冷/电源/数据中心基础设施"},
    {"symbol": "ORCL", "name_cn": "Oracle", "theme": "云/AI资本开支", "a_h_mapping": "AI服务器/光模块/PCB/数据中心"},
    {"symbol": "MSFT", "name_cn": "微软", "theme": "云/AI应用", "a_h_mapping": "AI算力需求/云资本开支"},
    {"symbol": "GOOGL", "name_cn": "谷歌A", "theme": "云/AI应用", "a_h_mapping": "AI算力需求/ASIC/服务器"},
    {"symbol": "AMZN", "name_cn": "亚马逊", "theme": "云/AI应用", "a_h_mapping": "AI算力需求/云资本开支"},
    {"symbol": "META", "name_cn": "Meta", "theme": "AI资本开支", "a_h_mapping": "AI服务器/光模块/CPO/PCB"},
    {"symbol": "AAPL", "name_cn": "苹果", "theme": "消费电子/AI终端", "a_h_mapping": "立讯精密/蓝思科技/舜宇光学/小米链"},
    {"symbol": "TSLA", "name_cn": "特斯拉", "theme": "FSD/机器人/新能源车", "a_h_mapping": "德赛西威/三花智控/拓普集团/机器人链"},
    {"symbol": "BABA", "name_cn": "阿里巴巴ADR", "theme": "中概互联网", "a_h_mapping": "09988 阿里巴巴-W/港股科技"},
    {"symbol": "JD", "name_cn": "京东ADR", "theme": "中概互联网", "a_h_mapping": "09618 京东集团-SW"},
    {"symbol": "BIDU", "name_cn": "百度ADR", "theme": "中概AI/自动驾驶", "a_h_mapping": "港股科技/智能驾驶"},
    {"symbol": "PDD", "name_cn": "拼多多", "theme": "中概消费互联网", "a_h_mapping": "消费/互联网风险偏好"},
    {"symbol": "LI", "name_cn": "理想汽车ADR", "theme": "中概新能源车", "a_h_mapping": "02015 理想汽车-W/汽车链"},
    {"symbol": "NIO", "name_cn": "蔚来ADR", "theme": "中概新能源车", "a_h_mapping": "港股汽车/新能源车"},
    {"symbol": "XPEV", "name_cn": "小鹏ADR", "theme": "中概新能源车/智驾", "a_h_mapping": "港股汽车/智驾"},
    {"symbol": "BILI", "name_cn": "哔哩哔哩ADR", "theme": "中概内容/游戏", "a_h_mapping": "09626 哔哩哔哩-W"},
    {"symbol": "TME", "name_cn": "腾讯音乐ADR", "theme": "中概音乐/内容", "a_h_mapping": "01698 腾讯音乐-SW"},
]

NEWS_KEYWORDS = [
    "英伟达", "NVIDIA", "H200", "Blackwell", "美光", "Micron", "台积电", "TSMC", "ASML", "AMD", "博通", "Marvell",
    "AI", "半导体", "存储", "HBM", "中概", "阿里", "特斯拉", "Tesla", "FSD", "Robotaxi", "关税", "出口管制", "港股", "美元", "美债", "降息",
]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_str(x: Any) -> str:
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def load_cookie() -> str:
    env_cookie = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env_cookie:
        return env_cookie.strip().strip('"').strip("'")
    if not COOKIE_FILE.exists():
        return ""
    text = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("XUEQIU_COOKIE="):
            line = line.split("=", 1)[1].strip()
        lines.append(line)
    return " ".join(lines).strip().strip('"').strip("'")


def build_session(logs: List[dict]) -> requests.Session:
    # 避免系统代理污染
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"

    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://xueqiu.com/",
        "Origin": "https://xueqiu.com",
    })
    cookie = load_cookie()
    if cookie:
        s.headers["Cookie"] = cookie
    try:
        r = s.get(XUEQIU_HOME, timeout=10)
        logs.append({"stage": "xueqiu_home", "api": XUEQIU_HOME, "status": r.status_code, "rows": 0, "error": ""})
    except Exception as e:
        logs.append({"stage": "xueqiu_home", "api": XUEQIU_HOME, "status": "ERROR", "rows": 0, "error": repr(e)})
    return s


def request_json(session: requests.Session, url: str, params: dict, retry: int = 1) -> Tuple[Optional[dict], Optional[str]]:
    last = None
    for i in range(retry + 1):
        try:
            r = session.get(url, params=params, timeout=12)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {(r.text or '')[:200]}"
            text = (r.text or "").strip()
            if not text.startswith("{"):
                return None, f"NOT_JSON: {text[:200]}"
            return r.json(), None
        except Exception as e:
            last = repr(e)
            time.sleep(0.3 * (i + 1))
    return None, last


def xq_candidates(symbol: str) -> List[str]:
    s = symbol.upper().strip()
    out = [s]
    if not s.startswith("US"):
        out.append("US" + s)
    # 雪球部分ETF/美股可能接受裸代码，部分接受US前缀，两个都试。
    return list(dict.fromkeys(out))


def quote_one(session: requests.Session, symbol: str) -> Tuple[Optional[dict], str, Optional[str]]:
    endpoints = [
        (f"{XUEQIU_BASE}/v5/stock/quote.json", {"extend": "detail"}),
        (f"{XUEQIU_BASE}/v5/stock/quote.json", {}),
        (f"{XUEQIU_BASE}/v5/stock/realtime/quotec.json", {}),
    ]
    errors = []
    for sym in xq_candidates(symbol):
        for url, extra in endpoints:
            params = {"symbol": sym, **extra}
            js, err = request_json(session, url, params, retry=1)
            if err:
                errors.append(f"{sym}:{url.split('/')[-1]}:{err}")
                continue
            data = (js or {}).get("data")
            q = None
            if isinstance(data, dict):
                q = data.get("quote") or data
            elif isinstance(data, list) and data:
                q = data[0]
            if isinstance(q, dict) and (q.get("current") is not None or q.get("symbol")):
                return q, sym, None
    return None, "", " ; ".join(errors[-4:])


def quote_batch(session: requests.Session, targets: List[dict], group_name: str, logs: List[dict]) -> pd.DataFrame:
    rows = []
    raw = {}
    for i, item in enumerate(targets, 1):
        sym = item["symbol"]
        q, used_sym, err = quote_one(session, sym)
        if q:
            raw[sym] = q
            rows.append({
                "symbol": sym,
                "xueqiu_symbol": used_sym or q.get("symbol"),
                "name_cn": item.get("name_cn", "") or q.get("name"),
                "theme": item.get("theme", ""),
                "a_h_mapping": item.get("a_h_mapping", ""),
                "price": safe_float(q.get("current")),
                "change": safe_float(q.get("chg") or q.get("change")),
                "change_pct": safe_float(q.get("percent")),
                "previous_close": safe_float(q.get("last_close") or q.get("pre_close")),
                "open": safe_float(q.get("open")),
                "high": safe_float(q.get("high")),
                "low": safe_float(q.get("low")),
                "volume": safe_float(q.get("volume")),
                "amount": safe_float(q.get("amount")),
                "market_cap": safe_float(q.get("market_capital")),
                "pe_ttm": safe_float(q.get("pe_ttm")),
                "currency": q.get("currency") or q.get("currency_unit"),
                "market_state": q.get("market_status") or q.get("status"),
                "source": "xueqiu",
                "error": "",
            })
        else:
            rows.append({
                "symbol": sym,
                "xueqiu_symbol": "",
                "name_cn": item.get("name_cn", ""),
                "theme": item.get("theme", ""),
                "a_h_mapping": item.get("a_h_mapping", ""),
                "price": None,
                "change": None,
                "change_pct": None,
                "previous_close": None,
                "open": None,
                "high": None,
                "low": None,
                "volume": None,
                "amount": None,
                "market_cap": None,
                "pe_ttm": None,
                "currency": None,
                "market_state": None,
                "source": "missing",
                "error": err or "missing",
            })
        logs.append({"stage": "quote", "api": "xueqiu_quote", "status": "OK" if q else "ERROR", "rows": 1 if q else 0, "symbol": sym, "used_symbol": used_sym, "error": err or ""})
        time.sleep(0.06)
    try:
        (RAW / f"{now_tag()}_{group_name}_xueqiu_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return pd.DataFrame(rows)


def find_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        if cand.lower() in lower:
            return lower[cand.lower()]
    for c in cols:
        cs = str(c).lower()
        for cand in candidates:
            if cand.lower() in cs:
                return c
    return None


def normalize_news(df: pd.DataFrame, source: str, hours: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    tc = find_col(list(df.columns), ["标题", "title", "新闻标题", "内容标题"])
    sc = find_col(list(df.columns), ["摘要", "内容", "summary", "description"])
    dc = find_col(list(df.columns), ["时间", "发布时间", "日期", "datetime", "time", "date"])
    rows = []
    for _, r in df.iterrows():
        title = safe_str(r.get(tc, "")) if tc else " | ".join(safe_str(x) for x in r.tolist() if safe_str(x))[:180]
        if not title:
            continue
        summary = safe_str(r.get(sc, "")) if sc else ""
        t = safe_str(r.get(dc, "")) if dc else ""
        text = f"{title} {summary}"
        if any(k.lower() in text.lower() for k in NEWS_KEYWORDS):
            rows.append({"time": t, "source": source, "title": title, "summary": summary[:250]})
    return pd.DataFrame(rows).head(160)


def fetch_news(hours: int, logs: List[dict]) -> pd.DataFrame:
    if ak is None:
        logs.append({"stage": "news", "api": "akshare", "status": "ERROR", "rows": 0, "error": "akshare not installed"})
        return pd.DataFrame()
    parts = []
    calls = [
        ("stock_info_global_futu", lambda: ak.stock_info_global_futu()),
        ("stock_info_global_cls", lambda: ak.stock_info_global_cls(symbol="全部")),
    ]
    for name, func in calls:
        try:
            df = func()
            try:
                df.to_csv(RAW / f"{now_tag()}_{name}.csv", index=False, encoding="utf-8-sig")
            except Exception:
                pass
            nd = normalize_news(df, name, hours)
            logs.append({"stage": "news", "api": name, "status": "OK", "rows": len(nd), "error": ""})
            parts.append(nd)
        except Exception as e:
            logs.append({"stage": "news", "api": name, "status": "ERROR", "rows": 0, "error": repr(e)})
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def md_table(df: pd.DataFrame, max_rows: int = 160) -> str:
    if df is None or df.empty:
        return "缺数据"
    w = df.head(max_rows).fillna("").copy()
    cols = list(w.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, r in w.iterrows():
        lines.append("| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ")[:300] for c in cols) + " |")
    return "\n".join(lines)


def build_theme_summary(core_df: pd.DataFrame) -> pd.DataFrame:
    if core_df.empty:
        return pd.DataFrame()
    df = core_df.copy()
    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")
    g = df.groupby("theme", dropna=False).agg(
        count=("symbol", "count"),
        valid_count=("change_pct", lambda s: int(s.notna().sum())),
        avg_change_pct=("change_pct", "mean"),
        max_change_pct=("change_pct", "max"),
        min_change_pct=("change_pct", "min"),
    ).reset_index().sort_values("avg_change_pct", ascending=False)
    for c in ["avg_change_pct", "max_change_pct", "min_change_pct"]:
        g[c] = pd.to_numeric(g[c], errors="coerce").round(3)
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    logs: List[dict] = []
    tag = now_tag()
    session = build_session(logs)

    etf_df = quote_batch(session, ETF_TARGETS, "etf_targets", logs)
    core_df = quote_batch(session, CORE_TARGETS, "core_targets", logs)
    theme_df = build_theme_summary(core_df)
    news_df = fetch_news(args.hours, logs)
    log_df = pd.DataFrame(logs)

    base = f"overnight_xueqiu_{tag}"
    etf_path = NORM / f"{base}_etf.csv"
    core_path = NORM / f"{base}_us_core.csv"
    theme_path = NORM / f"{base}_theme.csv"
    news_path = NORM / f"{base}_news.csv"
    log_path = LOG / f"{base}_log.csv"
    md_path = REP / f"{base}.md"

    etf_df.to_csv(etf_path, index=False, encoding="utf-8-sig")
    core_df.to_csv(core_path, index=False, encoding="utf-8-sig")
    theme_df.to_csv(theme_path, index=False, encoding="utf-8-sig")
    news_df.to_csv(news_path, index=False, encoding="utf-8-sig")
    log_df.to_csv(log_path, index=False, encoding="utf-8-sig")

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 隔夜外部变量自动输入包（雪球逐只查询版）\n\n")
        f.write("本文件自动逐只查询雪球美股/ETF核心池，并抓取隔夜相关新闻。程序只提供原始数据，不给交易结论。\n\n")
        f.write("## 1. 全球ETF/风险偏好代理\n\n")
        f.write(md_table(etf_df, 80)); f.write("\n\n")
        f.write("## 2. 美股/中概/AI链核心公司\n\n")
        f.write(md_table(core_df, 120)); f.write("\n\n")
        f.write("## 3. 主题分组平均涨跌\n\n")
        f.write(md_table(theme_df, 80)); f.write("\n\n")
        f.write("## 4. 隔夜相关新闻\n\n")
        f.write(md_table(news_df, 160)); f.write("\n\n")
        f.write("## 5. 接口日志\n\n")
        f.write(md_table(log_df, 200)); f.write("\n\n")
        f.write("## 6. 给 ChatGPT 的固定问题\n\n")
        f.write("- 外部变量偏 risk-on 还是 risk-off？\n")
        f.write("- 是否上修/下修 AI算力、半导体、存储、港股互联网？\n")
        f.write("- 哪些只是映射，不适合追？\n")
        f.write("- 哪些 A股/港股核心票需要进入盘前预测表？\n")

    print("[输出完成]")
    print("etf:", etf_path)
    print("core:", core_path)
    print("theme:", theme_path)
    print("news:", news_path)
    print("log:", log_path)
    print("md:", md_path)
    ok = int((core_df["source"] == "xueqiu").sum()) if not core_df.empty and "source" in core_df.columns else 0
    print(f"核心公司雪球成功：{ok}/{len(core_df)}")


if __name__ == "__main__":
    main()
