#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xueqiu_decision_capture_v2.py

雪球监测包生成器 v2
修正点：
1）固定读取脚本所在目录下的 watchlist_xueqiu.csv / xueqiu_cookie.txt；
2）启动时自动检测 Cookie；
3）先访问雪球首页，再请求 stock 接口；
4）batch 失败不崩溃，自动逐只抓取；
5）quote?extend=detail 失败时自动降级到 quote 和 realtime/quotec；
6）单只失败只记录 error，不让整个程序中断；
7）输出 Markdown + CSV，给 ChatGPT 判断是否买/等/取消。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests


SCRIPT_DIR = Path(__file__).resolve().parent
XUEQIU_BASE = "https://stock.xueqiu.com"
XUEQIU_HOME = "https://xueqiu.com/"
DEFAULT_COOKIE_FILE = "xueqiu_cookie.txt"
DEFAULT_WATCHLIST_FILE = "watchlist_xueqiu.csv"


DEFAULT_WATCHLIST = [
    ("SH688008", "澜起科技", "存储/HBM"),
    ("SH688041", "海光信息", "国产算力/AI芯片"),
    ("SH603986", "兆易创新", "存储/MCU"),
    ("SH688525", "佰维存储", "存储弹性"),
    ("SH688047", "龙芯中科", "国产CPU"),
    ("SH688981", "中芯国际A", "晶圆制造"),
    ("00981", "中芯国际H", "晶圆制造"),
    ("SH601138", "工业富联", "AI服务器"),
    ("SZ002463", "沪电股份", "AI PCB"),
    ("SZ300476", "胜宏科技", "AI PCB"),
    ("SZ300394", "天孚通信", "CPO/光模块"),
    ("SZ300308", "中际旭创", "CPO/光模块"),
    ("SZ300502", "新易盛", "CPO/光模块"),
    ("SH688256", "寒武纪", "AI芯片"),
    ("SZ002371", "北方华创", "半导体设备"),
    ("SH688012", "中微公司", "半导体设备"),
]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dirs() -> Tuple[Path, Path]:
    data_dir = SCRIPT_DIR / "data_xueqiu"
    out_dir = SCRIPT_DIR / "outputs_xueqiu"
    data_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    return data_dir, out_dir


def normalize_xueqiu_symbol(symbol: str) -> str:
    """
    雪球 symbol 规范化：
    - A股：SH688008 / SZ300502
    - 港股：00981 / 01810 / 00700（雪球网页也是 /S/00981）
    重要：不要把港股转成 00981。00981 是新浪等数据源常见写法，
    但雪球 stock.xueqiu.com 的 quote 接口通常使用纯 5 位港股代码。
    """
    s = str(symbol).strip().upper()
    if not s:
        return s

    if s.startswith(("SH", "SZ", "US")):
        return s

    # 用户若写 00981，转成雪球需要的 00981
    if s.startswith("HK") and s[2:].isdigit():
        return s[2:].zfill(5)

    # 港股裸代码：981 / 0981 / 00981 -> 00981
    if s.isdigit():
        return s.zfill(5)

    return s


def create_default_watchlist(path: Path) -> None:
    if path.exists():
        return
    df = pd.DataFrame(DEFAULT_WATCHLIST, columns=["symbol", "name", "theme"])
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[INFO] 已创建默认自选表：{path}")


def load_watchlist(path: Path) -> pd.DataFrame:
    create_default_watchlist(path)
    df = pd.read_csv(path, dtype=str).fillna("")
    required = {"symbol", "name", "theme"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"watchlist 缺少列：{missing}，需要 symbol,name,theme")
    df["symbol"] = df["symbol"].str.strip().map(normalize_xueqiu_symbol)
    df["name"] = df["name"].str.strip()
    df["theme"] = df["theme"].str.strip()
    df = df[df["symbol"] != ""].copy()
    return df


def load_cookie(cookie_file: str = DEFAULT_COOKIE_FILE) -> str:
    env_cookie = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env_cookie:
        print("[INFO] 已从环境变量 XUEQIU_COOKIE 读取 Cookie")
        return env_cookie.strip().strip('"').strip("'")

    path = Path(cookie_file)
    if not path.is_absolute():
        path = SCRIPT_DIR / path

    if not path.exists():
        print(f"[INFO] 未找到 Cookie 文件：{path}")
        return ""

    text = path.read_text(encoding="utf-8", errors="ignore").strip()

    # 兼容 XUEQIU_COOKIE=xxx 写法
    if text.startswith("XUEQIU_COOKIE="):
        text = text.split("=", 1)[1].strip()

    # 删除注释/空行
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    text = " ".join(lines).strip().strip('"').strip("'").strip()

    if "这里粘贴" in text or "你的完整雪球Cookie" in text:
        print("[WARN] Cookie 文件仍是模板占位符，没有加载。")
        return ""

    if text:
        print(f"[INFO] 已从 {path} 读取 Cookie，长度={len(text)}，xq_a_token={'xq_a_token=' in text}")
    return text


def build_session(cookie_file: str = DEFAULT_COOKIE_FILE) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://xueqiu.com/S/SH688008",
        "Origin": "https://xueqiu.com",
        "Connection": "keep-alive",
    })
    cookie = load_cookie(cookie_file)
    if cookie:
        s.headers.update({"Cookie": cookie})

    # 先访问首页，刷新 waf / 基础 cookie
    try:
        r = s.get(XUEQIU_HOME, timeout=10)
        print(f"[INFO] 雪球首页状态码：{r.status_code}")
    except Exception as e:
        print(f"[WARN] 访问雪球首页失败：{e}")

    return s


def request_json(session: requests.Session, url: str, params: Optional[dict] = None, retry: int = 1) -> Tuple[Optional[dict], Optional[str]]:
    last_error = None
    for i in range(retry + 1):
        try:
            r = session.get(url, params=params, timeout=12)
            if r.status_code != 200:
                body = (r.text or "")[:300].replace("\n", " ")
                return None, f"HTTP {r.status_code}; body={body}"
            text = r.text.strip()
            if not text:
                return None, "empty body"
            return r.json(), None
        except Exception as e:
            last_error = repr(e)
            time.sleep(0.4 * (i + 1))
    return None, last_error


def fetch_batch_quote(session: requests.Session, symbols: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not symbols:
        return out

    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i+20]
        url = f"{XUEQIU_BASE}/v5/stock/batch/quote.json"
        js, err = request_json(session, url, {"symbol": ",".join(chunk), "extend": "detail"})
        if err:
            print(f"[WARN] batch quote失败，改为逐只抓取。symbols={chunk} err={err}")
            continue
        items = ((js or {}).get("data") or {}).get("items", [])
        for it in items:
            if not isinstance(it, dict):
                continue
            quote = it.get("quote")
            if not isinstance(quote, dict):
                # 雪球偶尔会返回 quote:null，不能让整个程序崩
                continue
            sym = quote.get("symbol")
            if sym:
                out[sym] = quote
    return out


def fetch_quote(session: requests.Session, symbol: str) -> Tuple[Optional[dict], Optional[str]]:
    url = f"{XUEQIU_BASE}/v5/stock/quote.json"

    # 1）优先 detail
    js, err = request_json(session, url, {"symbol": symbol, "extend": "detail"})
    if not err and js:
        quote = ((js or {}).get("data") or {}).get("quote")
        if quote:
            return quote, None

    # 2）降级无 extend
    js2, err2 = request_json(session, url, {"symbol": symbol})
    if not err2 and js2:
        quote = ((js2 or {}).get("data") or {}).get("quote")
        if quote:
            return quote, None

    # 3）再降级 realtime/quotec
    url2 = f"{XUEQIU_BASE}/v5/stock/realtime/quotec.json"
    js3, err3 = request_json(session, url2, {"symbol": symbol})
    if not err3 and js3:
        data = (js3 or {}).get("data")
        if isinstance(data, list) and data:
            return data[0], None

    return None, f"detail={err}; no_extend={err2}; quotec={err3}"


def fetch_realtime_quotec_batch(session: requests.Session, symbols: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    url = f"{XUEQIU_BASE}/v5/stock/realtime/quotec.json"
    for i in range(0, len(symbols), 30):
        chunk = symbols[i:i+30]
        js, err = request_json(session, url, {"symbol": ",".join(chunk)})
        if err:
            print(f"[WARN] realtime quotec batch失败：{chunk} err={err}")
            continue
        data = (js or {}).get("data", [])
        if isinstance(data, list):
            for q in data:
                sym = q.get("symbol")
                if sym:
                    out[sym] = q
    return out


def fetch_kline(session: requests.Session, symbol: str, count: int = 120) -> Optional[pd.DataFrame]:
    url = f"{XUEQIU_BASE}/v5/stock/chart/kline.json"
    params = {
        "symbol": symbol,
        "begin": int(time.time() * 1000),
        "period": "day",
        "type": "before",
        "count": -abs(count),
        "indicator": "kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance",
    }
    js, err = request_json(session, url, params, retry=1)
    if err or not js:
        print(f"[WARN] 日K失败 {symbol}: {err}")
        return None
    data = js.get("data", {})
    cols = data.get("column", [])
    items = data.get("item", [])
    if not cols or not items:
        return None
    df = pd.DataFrame(items, columns=cols)
    if "timestamp" in df.columns:
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce").dt.strftime("%Y-%m-%d")
    for c in df.columns:
        if c != "date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


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


def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1) * 100


def calc_kline_features(kdf: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """
    输出给交易判断用的历史结构特征：
    - 1/3/5/10/20/40/60/120日涨跌幅
    - MA3/5/10/20/40/60/120/250 与偏离
    - 5/10/20/60日高低位、当前位置、距高点回撤、距低点反弹
    - 成交额均量、量能相对值
    - ATR14 近似波动，用于判断止损/回撤容忍
    """
    f: Dict[str, Any] = {}
    if kdf is None or kdf.empty or "close" not in kdf.columns:
        return f

    df = kdf.dropna(subset=["close"]).copy()
    if df.empty:
        return f

    for col in ["open", "high", "low", "close", "amount", "volume", "turnoverrate"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    close = df["close"].dropna()
    if close.empty:
        return f

    last_close = safe_float(close.iloc[-1])
    f["hist_days"] = len(close)

    for n in [1, 3, 5, 10, 20, 40, 60, 120]:
        if len(close) > n:
            f[f"ret_{n}d_pct"] = pct(last_close, safe_float(close.iloc[-1-n]))

    for n in [3, 5, 10, 20, 40, 60, 120, 250]:
        if len(close) >= n:
            ma = safe_float(close.tail(n).mean())
            f[f"ma{n}"] = ma
            f[f"dist_ma{n}_pct"] = pct(last_close, ma)

    if "high" in df.columns and "low" in df.columns:
        for n in [5, 10, 20, 40, 60, 120]:
            if len(df) >= n:
                h = safe_float(df["high"].tail(n).max())
                l = safe_float(df["low"].tail(n).min())
                f[f"high_{n}d"] = h
                f[f"low_{n}d"] = l
                if last_close and h:
                    f[f"drawdown_from_high_{n}d_pct"] = pct(last_close, h)
                if last_close and l:
                    f[f"rebound_from_low_{n}d_pct"] = pct(last_close, l)
                if last_close and h and l and h != l:
                    f[f"pos_{n}d_pct"] = (last_close - l) / (h - l) * 100

        # ATR14 近似：真实波幅均值 / 收盘价
        if len(df) >= 15:
            prev_close = df["close"].shift(1)
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - prev_close).abs()
            tr3 = (df["low"] - prev_close).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr14 = safe_float(tr.tail(14).mean())
            f["atr14"] = atr14
            if atr14 and last_close:
                f["atr14_pct"] = atr14 / last_close * 100

    if "amount" in df.columns:
        amount = df["amount"].dropna()
        if not amount.empty:
            f["amount_last"] = safe_float(amount.iloc[-1])
        for n in [5, 10, 20, 60]:
            if len(amount) >= n:
                ma_amt = safe_float(amount.tail(n).mean())
                f[f"amount_ma{n}"] = ma_amt
                if ma_amt and f.get("amount_last"):
                    f[f"amount_vs_ma{n}"] = f["amount_last"] / ma_amt
        if len(amount) >= 20 and f.get("amount_last") is not None:
            hist = amount.tail(60)
            f["amount_rank_60d_pct"] = float((hist <= f["amount_last"]).mean() * 100)

    # 连续涨跌天数
    if len(close) >= 2:
        diff = close.diff()
        up = 0
        down = 0
        for v in reversed(diff.dropna().tolist()):
            if v > 0 and down == 0:
                up += 1
            elif v < 0 and up == 0:
                down += 1
            else:
                break
        f["consecutive_up_days"] = up
        f["consecutive_down_days"] = down

    return f


def calc_realtime_features(q: dict) -> Dict[str, Any]:
    f: Dict[str, Any] = {}
    current = safe_float(q.get("current"))
    open_ = safe_float(q.get("open"))
    high = safe_float(q.get("high"))
    low = safe_float(q.get("low"))
    pre_close = safe_float(q.get("last_close") or q.get("pre_close"))
    percent = safe_float(q.get("percent"))

    f["current"] = current
    f["open"] = open_
    f["high"] = high
    f["low"] = low
    f["pre_close"] = pre_close
    f["percent"] = percent
    f["open_gap_pct"] = pct(open_, pre_close)
    f["from_open_pct"] = pct(current, open_)
    f["from_low_pct"] = pct(current, low)
    f["from_high_pct"] = pct(current, high)
    f["intraday_amp_pct"] = ((high - low) / pre_close * 100) if high and low and pre_close else None
    return f


def decision_tags(row: dict) -> Dict[str, str]:
    pct_now = safe_float(row.get("percent"))
    open_gap = safe_float(row.get("open_gap_pct"))
    from_open = safe_float(row.get("from_open_pct"))
    from_high = safe_float(row.get("from_high_pct"))
    dist_ma5 = safe_float(row.get("dist_ma5_pct"))
    dist_ma20 = safe_float(row.get("dist_ma20_pct"))
    amount_ratio = safe_float(row.get("amount_vs_ma20"))
    pos20 = safe_float(row.get("pos_20d_pct"))

    strength, risk, hint = [], [], []

    if pct_now is not None:
        if pct_now >= 5:
            strength.append("强势")
            risk.append("追高风险")
        elif pct_now >= 2:
            strength.append("偏强")
        elif pct_now >= 0:
            strength.append("温和")
        else:
            strength.append("转弱")

    if open_gap is not None:
        if open_gap >= 4:
            risk.append("高开过猛")
        elif 0 <= open_gap <= 2.5:
            hint.append("开盘幅度健康")
        elif open_gap < -1:
            risk.append("低开需验证承接")

    if from_open is not None:
        if from_open > 0.8:
            strength.append("开盘后走强")
        elif from_open < -1:
            risk.append("开盘后走弱")

    if from_high is not None and from_high < -2:
        risk.append("冲高回落")

    if amount_ratio is not None:
        if amount_ratio >= 2:
            strength.append("放量")
        elif amount_ratio < 0.7:
            risk.append("量能不足")

    if dist_ma5 is not None and dist_ma5 > 8:
        risk.append("偏离5日线过远")
    if dist_ma20 is not None and dist_ma20 > 25:
        risk.append("偏离20日线过远")
    if pos20 is not None:
        if pos20 >= 90:
            risk.append("20日高位")
        elif pos20 <= 40:
            hint.append("位置不高")

    if "高开过猛" in risk or "追高风险" in risk:
        hint.append("等回踩承接")
    elif "偏强" in strength or "强势" in strength:
        hint.append("候选买点")
    else:
        hint.append("等确认")

    return {
        "strength_tags": "；".join(dict.fromkeys(strength)),
        "risk_tags": "；".join(dict.fromkeys(risk)),
        "action_hint": "；".join(dict.fromkeys(hint)),
    }



def clean_kline_for_export(kdf: Optional[pd.DataFrame]) -> pd.DataFrame:
    """
    把雪球原始 kline 清洗成适合 Excel / ChatGPT 读取的历史K线表。
    解决问题：
    1）date 不再放最后，固定放第一列；
    2）删除 pe/pb/ps/pcf/market_capital/balance/hold_* 等杂项字段；
    3）补充 MA、涨跌幅、区间位置、量能相对值；
    4）列顺序固定，避免 Excel 看起来错位。
    """
    if kdf is None or kdf.empty:
        return pd.DataFrame()

    df = kdf.copy()

    # 生成 date，并固定放第一列
    if "date" not in df.columns:
        if "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            df["date"] = ""

    for col in ["open", "high", "low", "close", "chg", "percent", "volume", "amount", "turnoverrate"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 常用均线
    if "close" in df.columns:
        for n in [3, 5, 10, 20, 40, 60, 120, 250]:
            if len(df) >= n:
                df[f"ma{n}"] = df["close"].rolling(n).mean()
                df[f"dist_ma{n}_pct"] = (df["close"] / df[f"ma{n}"] - 1) * 100

        for n in [1, 3, 5, 10, 20, 40, 60, 120]:
            df[f"ret_{n}d_pct"] = (df["close"] / df["close"].shift(n) - 1) * 100

    # 区间高低位
    if {"high", "low", "close"}.issubset(df.columns):
        for n in [5, 10, 20, 40, 60, 120]:
            df[f"high_{n}d"] = df["high"].rolling(n).max()
            df[f"low_{n}d"] = df["low"].rolling(n).min()
            df[f"drawdown_from_high_{n}d_pct"] = (df["close"] / df[f"high_{n}d"] - 1) * 100
            df[f"rebound_from_low_{n}d_pct"] = (df["close"] / df[f"low_{n}d"] - 1) * 100
            denom = (df[f"high_{n}d"] - df[f"low_{n}d"])
            df[f"pos_{n}d_pct"] = ((df["close"] - df[f"low_{n}d"]) / denom.replace(0, pd.NA)) * 100

        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        df["atr14_pct"] = df["atr14"] / df["close"] * 100

    # 量能
    if "amount" in df.columns:
        for n in [5, 10, 20, 60]:
            df[f"amount_ma{n}"] = df["amount"].rolling(n).mean()
            df[f"amount_vs_ma{n}"] = df["amount"] / df[f"amount_ma{n}"]

    # 固定列顺序：先基础行情，再趋势/位置/量能
    preferred = [
        "date", "open", "high", "low", "close", "chg", "percent",
        "volume", "amount", "turnoverrate",
        "ma3", "ma5", "ma10", "ma20", "ma40", "ma60", "ma120", "ma250",
        "dist_ma5_pct", "dist_ma10_pct", "dist_ma20_pct", "dist_ma40_pct", "dist_ma60_pct", "dist_ma120_pct",
        "ret_1d_pct", "ret_3d_pct", "ret_5d_pct", "ret_10d_pct", "ret_20d_pct", "ret_40d_pct", "ret_60d_pct", "ret_120d_pct",
        "high_20d", "low_20d", "pos_20d_pct", "drawdown_from_high_20d_pct", "rebound_from_low_20d_pct",
        "high_60d", "low_60d", "pos_60d_pct", "drawdown_from_high_60d_pct", "rebound_from_low_60d_pct",
        "atr14", "atr14_pct",
        "amount_ma5", "amount_ma10", "amount_ma20", "amount_ma60",
        "amount_vs_ma5", "amount_vs_ma10", "amount_vs_ma20", "amount_vs_ma60",
    ]

    cols = [c for c in preferred if c in df.columns]
    out = df[cols].copy()

    # 数值保留，文件更小更清楚
    for c in out.columns:
        if c != "date":
            out[c] = pd.to_numeric(out[c], errors="coerce").round(4)

    return out


def collect(watchlist_path: Path, cookie_file: str, kline_count: int) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    ensure_dirs()
    wl = load_watchlist(watchlist_path)
    symbols = wl["symbol"].tolist()

    print("[INFO] 当前自选列表：", ",".join(symbols))

    session = build_session(cookie_file)

    # 先尝试 batch detail，失败也不影响
    batch_detail = fetch_batch_quote(session, symbols)
    print(f"[INFO] batch detail 成功数量：{len(batch_detail)} / {len(symbols)}")
    if not batch_detail:
        # 再尝试实时轻量接口
        batch_detail = fetch_realtime_quotec_batch(session, symbols)
        print(f"[INFO] realtime quotec 成功数量：{len(batch_detail)} / {len(symbols)}")

    rows: List[dict] = []
    raw: Dict[str, Any] = {"errors": [], "quotes": {}, "kline_tail": {}}

    for _, item in wl.iterrows():
        symbol, name, theme = item["symbol"], item["name"], item["theme"]
        q = batch_detail.get(symbol)
        err = None
        if not q:
            q, err = fetch_quote(session, symbol)

        if not q:
            print(f"[WARN] 行情失败 {symbol} {name}: {err}")
            rows.append({
                "capture_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "name": name, "theme": theme, "error": err
            })
            raw["errors"].append({"symbol": symbol, "name": name, "error": err})
            continue

        kdf = fetch_kline(session, symbol, count=kline_count)

        # 保存清洗后的完整日K，方便你发给 ChatGPT 做更深分析
        if kdf is not None and not kdf.empty:
            hist_dir = SCRIPT_DIR / "outputs_xueqiu" / "history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            safe_name = str(name or symbol).replace("/", "_").replace("\\", "_")
            hist_path = hist_dir / f"{symbol}_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_kline_clean.csv"
            clean_hist = clean_kline_for_export(kdf)
            clean_hist.to_csv(hist_path, index=False, encoding="utf-8-sig")

        row: Dict[str, Any] = {
            "capture_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "name": name or q.get("name"),
            "theme": theme,
        }

        for k in [
            "current", "percent", "chg", "open", "high", "low", "last_close", "volume",
            "amount", "turnover_rate", "amplitude", "market_capital", "float_market_capital",
            "volume_ratio", "pe_ttm", "pb", "eps"
        ]:
            if k in q:
                row[k] = q.get(k)

        row.update(calc_realtime_features(q))
        row.update(calc_kline_features(kdf))
        row.update(decision_tags(row))
        rows.append(row)
        raw["quotes"][symbol] = q

        if kdf is not None and not kdf.empty:
            keep = [c for c in ["date", "open", "high", "low", "close", "percent", "amount", "turnoverrate"] if c in kdf.columns]
            raw["kline_tail"][symbol] = kdf[keep].tail(10).to_dict(orient="records")

        time.sleep(0.15)

    df = pd.DataFrame(rows)

    # 数值转换
    for c in df.columns:
        if c not in ["capture_time", "symbol", "name", "theme", "strength_tags", "risk_tags", "action_hint", "error"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    theme_df = build_theme_summary(df)
    return df, theme_df, raw


def build_theme_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "theme" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    for c in ["percent", "amount", "amount_vs_ma20"]:
        if c not in work.columns:
            work[c] = pd.NA
        work[c] = pd.to_numeric(work[c], errors="coerce")

    g = work.groupby("theme", dropna=False).agg(
        count=("symbol", "count"),
        avg_percent=("percent", "mean"),
        max_percent=("percent", "max"),
        strong_count=("percent", lambda s: int((pd.to_numeric(s, errors="coerce") >= 2).sum())),
        amount_sum=("amount", "sum"),
        avg_amount_vs_ma20=("amount_vs_ma20", "mean"),
    ).reset_index()

    g = g.sort_values(["avg_percent", "strong_count", "amount_sum"], ascending=[False, False, False])
    return g



def df_to_markdown_safe(df: pd.DataFrame) -> str:
    """
    不依赖 tabulate 的 Markdown 表格输出。
    pandas.DataFrame.to_markdown 需要额外安装 tabulate；这里直接手写，避免运行崩溃。
    """
    if df is None or df.empty:
        return "缺数据"

    work = df.copy()
    work = work.fillna("")
    cols = list(work.columns)

    def fmt(x: Any) -> str:
        s = str(x)
        s = s.replace("\n", " ").replace("|", "/")
        return s

    header = "| " + " | ".join(fmt(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for _, r in work.iterrows():
        rows.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join([header, sep] + rows)


def build_markdown(df: pd.DataFrame, theme_df: pd.DataFrame) -> str:
    lines = []
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# 雪球监测数据包 {t}")
    lines.append("")
    lines.append("## 1. 板块强弱摘要")
    if not theme_df.empty:
        show = theme_df.copy()
        for c in ["avg_percent", "max_percent", "amount_sum", "avg_amount_vs_ma20"]:
            if c in show.columns:
                show[c] = pd.to_numeric(show[c], errors="coerce").round(3)
        lines.append(df_to_markdown_safe(show))
    else:
        lines.append("缺数据")

    lines.append("")
    lines.append("## 2. 个股监测总表")
    keep = [
        "symbol", "name", "theme", "current", "percent", "open_gap_pct", "from_open_pct",
        "from_low_pct", "from_high_pct", "ret_3d_pct", "ret_5d_pct", "ret_10d_pct", "ret_20d_pct",
        "dist_ma5_pct", "dist_ma10_pct", "dist_ma20_pct", "dist_ma60_pct",
        "ret_40d_pct", "ret_60d_pct", "drawdown_from_high_20d_pct", "rebound_from_low_20d_pct",
        "amount_vs_ma5", "amount_vs_ma10", "amount_vs_ma20", "amount_vs_ma60",
        "pos_20d_pct", "pos_60d_pct", "atr14_pct", "turnover_rate", "volume_ratio", "amount",
        "strength_tags", "risk_tags", "action_hint", "error"
    ]
    keep = [c for c in keep if c in df.columns]
    show = df[keep].copy()
    for c in show.columns:
        if c not in ["symbol", "name", "theme", "strength_tags", "risk_tags", "action_hint", "error"]:
            show[c] = pd.to_numeric(show[c], errors="coerce").round(3)
    if not show.empty:
        lines.append(df_to_markdown_safe(show))
    else:
        lines.append("缺数据")

    lines.append("")
    lines.append("## 3. 请 ChatGPT 判断")
    lines.append("- 当前主线是否成立？最强主线是哪条？")
    lines.append("- 哪些票能买第一笔，哪些只能等回踩，哪些取消？")
    lines.append("- 如果目标不是今天必赚，而是近几日/1-2周/1个月，收益回撤是否合格？")
    lines.append("- 买入区、确认加仓区、失效位、仓位是多少？")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="雪球监测包生成器 v2")
    parser.add_argument("--watchlist", default=DEFAULT_WATCHLIST_FILE)
    parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    parser.add_argument("--kline-count", type=int, default=120)
    args = parser.parse_args()

    watchlist_path = Path(args.watchlist)
    if not watchlist_path.is_absolute():
        watchlist_path = SCRIPT_DIR / watchlist_path

    df, theme_df, raw = collect(watchlist_path, args.cookie_file, args.kline_count)

    out_dir = SCRIPT_DIR / "outputs_xueqiu"
    out_dir.mkdir(exist_ok=True)
    tag = now_tag()

    csv_path = out_dir / f"xueqiu_capture_{tag}.csv"
    theme_path = out_dir / f"xueqiu_theme_{tag}.csv"
    md_path = out_dir / f"xueqiu_packet_{tag}.md"
    raw_path = out_dir / f"xueqiu_raw_{tag}.json"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    theme_df.to_csv(theme_path, index=False, encoding="utf-8-sig")
    md_path.write_text(build_markdown(df, theme_df), encoding="utf-8")
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n完成。把下面两个文件发给 ChatGPT：")
    print(f"1）{md_path}")
    print(f"2）{csv_path}")
    print("\n板块摘要：")
    if not theme_df.empty:
        print(theme_df.to_string(index=False))
    else:
        print("缺数据")


if __name__ == "__main__":
    main()
