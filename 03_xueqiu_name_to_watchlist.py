#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xueqiu_name_to_watchlist_v2.py

结论：
雪球搜索接口容易 403，所以这个版本默认不依赖搜索。
优先使用本地“中文名 -> 雪球代码”映射表；未知股票才可选择启用 --use-search 尝试雪球搜索。

输入：
    names_input.txt

支持格式：
    工业富联
    工业富联,AI服务器
    中芯国际,晶圆制造,A
    中芯国际,晶圆制造,HK
    澜起科技,存储/HBM,SH688008

输出：
    outputs_xueqiu/watchlist_from_names_时间.csv
    outputs_xueqiu/name_candidates_时间.csv

使用：
    python xueqiu_name_to_watchlist_v2.py
    copy "outputs_xueqiu\watchlist_from_names_时间.csv" "watchlist_xueqiu.csv"

注意：
雪球港股代码使用 5 位纯数字，例如：
    小米集团-W -> 01810
    中芯国际H -> 00981
不要写 HK01810 / HK00981。
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


SCRIPT_DIR = Path(__file__).resolve().parent
COOKIE_FILE = SCRIPT_DIR / "xueqiu_cookie.txt"
INPUT_FILE = SCRIPT_DIR / "names_input.txt"
OUT_DIR = SCRIPT_DIR / "outputs_xueqiu"
DEFAULT_WATCHLIST_FILE = "watchlist_xueqiu.csv"
OUT_DIR.mkdir(exist_ok=True)


# 你常用池子的本地映射。后面有新股票，直接往这里加。
# 同一家公司 A/H 都有时，用 hint=A 或 hint=HK 区分。
LOCAL_MAP: Dict[str, Dict[str, str]] = {
    # A股 AI硬件 / 半导体
    "澜起科技": {"A": "SH688008"},
    "海光信息": {"A": "SH688041"},
    "兆易创新": {"A": "SH603986"},
    "佰维存储": {"A": "SH688525"},
    "龙芯中科": {"A": "SH688047"},
    "中芯国际": {"A": "SH688981", "HK": "00981"},
    "中芯国际A": {"A": "SH688981"},
    "中芯国际H": {"HK": "00981"},
    "工业富联": {"A": "SH601138"},
    "沪电股份": {"A": "SZ002463"},
    "胜宏科技": {"A": "SZ300476"},
    "天孚通信": {"A": "SZ300394"},
    "中际旭创": {"A": "SZ300308"},
    "新易盛": {"A": "SZ300502"},
    "寒武纪": {"A": "SH688256"},
    "北方华创": {"A": "SZ002371"},
    "中微公司": {"A": "SH688012"},
    "拓荆科技": {"A": "SH688072"},
    "华海清科": {"A": "SH688120"},
    "雅克科技": {"A": "SZ002409"},
    "鼎龙股份": {"A": "SZ300054"},
    "兴森科技": {"A": "SZ002436"},
    "通富微电": {"A": "SZ002156"},
    "长电科技": {"A": "SH600584"},
    "江波龙": {"A": "SZ301308"},
    "中科曙光": {"A": "SH603019"},
    "大华股份": {"A": "SZ002236"},
    "德赛西威": {"A": "SZ002920"},

    # 港股互联网 / 消费 / 医药 / 汽车
    "美图公司": {"HK": "01357"},
    "美图": {"HK": "01357"},
    "小米集团-W": {"HK": "01810"},
    "小米集团": {"HK": "01810"},
    "美团-W": {"HK": "03690"},
    "美团": {"HK": "03690"},
    "阿里巴巴-W": {"HK": "09988"},
    "阿里巴巴": {"HK": "09988"},
    "腾讯控股": {"HK": "00700"},
    "理想汽车-W": {"HK": "02015"},
    "理想汽车": {"HK": "02015"},
    "泡泡玛特": {"HK": "09992"},
    "百济神州": {"HK": "06160"},
    "金斯瑞生物科技": {"HK": "01548"},
    "金斯瑞": {"HK": "01548"},
    "京东集团-SW": {"HK": "09618"},
    "京东集团": {"HK": "09618"},
    "京东": {"HK": "09618"},
    "腾讯音乐-SW": {"HK": "01698"},
    "腾讯音乐": {"HK": "01698"},
    "哔哩哔哩-W": {"HK": "09626"},
    "哔哩哔哩": {"HK": "09626"},
    "B站": {"HK": "09626"},
    "舜宇光学科技": {"HK": "02382"},
    "舜宇光学": {"HK": "02382"},
    "快手-W": {"HK": "01024"},
    "快手": {"HK": "01024"},

    # 机器人 / 汽车链
    "三花智控": {"A": "SZ002050"},
    "拓普集团": {"A": "SH601689"},
    "绿的谐波": {"A": "SH688017"},
    "鸣志电器": {"A": "SH603728"},
    "五洲新春": {"A": "SH603667"},
    "恒立液压": {"A": "SH601100"},
}


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_name(name: str) -> str:
    s = str(name).strip()
    s = s.replace("**", "")
    s = s.replace("－", "-").replace("—", "-").replace("–", "-")
    s = re.sub(r"\s+", "", s)
    return s


def normalize_symbol(s: str) -> str:
    s = str(s).strip().upper()
    if not s:
        return s
    if s.startswith(("SH", "SZ", "US")):
        return s
    if s.startswith("HK") and s[2:].isdigit():
        return s[2:].zfill(5)
    if s.isdigit():
        return s.zfill(5)
    return s


def is_symbol(s: str) -> bool:
    s = str(s).strip().upper()
    return bool(
        re.match(r"^(SH|SZ)\d{6}$", s)
        or re.match(r"^HK\d{4,5}$", s)
        or re.match(r"^\d{3,5}$", s)
        or re.match(r"^US[A-Z0-9.]+$", s)
    )


def default_market_for_symbol(sym: str) -> str:
    sym = normalize_symbol(sym)
    if sym.startswith(("SH", "SZ")):
        return "A"
    if sym.isdigit():
        return "HK"
    if sym.startswith("US"):
        return "US"
    return ""


def load_cookie() -> str:
    env = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env:
        return env

    if not COOKIE_FILE.exists():
        return ""

    text = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    if text.startswith("XUEQIU_COOKIE="):
        text = text.split("=", 1)[1].strip()

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return " ".join(lines).strip().strip('"').strip("'")


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
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
        s.get("https://xueqiu.com/", timeout=10)
    except Exception:
        pass
    return s


def parse_input() -> List[Dict[str, str]]:
    if not INPUT_FILE.exists():
        INPUT_FILE.write_text(
            "# 格式：股票名,主题,市场或代码\n"
            "澜起科技,存储/HBM,A\n"
            "海光信息,国产算力/AI芯片,A\n"
            "兆易创新,存储/MCU,A\n"
            "工业富联,AI服务器,A\n"
            "沪电股份,AI PCB,A\n"
            "新易盛,CPO/光模块,A\n"
            "北方华创,半导体设备,A\n"
            "中芯国际,晶圆制造,A\n"
            "中芯国际,晶圆制造,HK\n",
            encoding="utf-8-sig",
        )

    rows = []
    for raw in INPUT_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(",")]
        name = clean_name(parts[0]) if len(parts) >= 1 else ""
        theme = parts[1].strip() if len(parts) >= 2 else ""
        hint = parts[2].strip().upper() if len(parts) >= 3 else ""
        if name:
            rows.append({"input_name": name, "theme": theme, "hint": hint})
    return rows


def local_lookup(name: str, hint: str = "") -> Optional[str]:
    name = clean_name(name)
    hint_u = str(hint).strip().upper()

    if hint_u and is_symbol(hint_u):
        return normalize_symbol(hint_u)

    entry = LOCAL_MAP.get(name)
    if not entry:
        return None

    if hint_u in ("A", "HK", "US"):
        return entry.get(hint_u)

    # 默认：如果只有一个市场，直接返回；如果 A/H 都有，默认 A。
    if len(entry) == 1:
        return list(entry.values())[0]
    return entry.get("A") or list(entry.values())[0]


def req_json(s: requests.Session, url: str, params: dict) -> Optional[dict]:
    try:
        r = s.get(url, params=params, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def extract_candidates(obj: Any) -> List[Dict[str, Any]]:
    out = []
    if isinstance(obj, dict):
        keys = set(obj.keys())
        if ("symbol" in keys or "code" in keys) and ("name" in keys or "stockName" in keys):
            name = obj.get("name") or obj.get("stockName") or obj.get("title") or ""
            symbol = obj.get("symbol") or obj.get("code") or ""
            if symbol and name:
                out.append({
                    "symbol": normalize_symbol(symbol),
                    "name": str(name).strip(),
                    "raw": json.dumps(obj, ensure_ascii=False)[:500],
                })
        for v in obj.values():
            out.extend(extract_candidates(v))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(extract_candidates(x))
    return out


def search_xueqiu(s: requests.Session, keyword: str) -> List[Dict[str, Any]]:
    endpoints = [
        ("https://stock.xueqiu.com/v5/stock/search.json", {"keyword": keyword, "page": 1, "size": 10}),
        ("https://stock.xueqiu.com/v5/stock/search.json", {"code": keyword, "page": 1, "size": 10}),
        ("https://xueqiu.com/query/v1/search/stock.json", {"q": keyword, "count": 10}),
    ]

    all_cands = []
    for url, params in endpoints:
        js = req_json(s, url, params)
        if not js:
            continue
        cands = extract_candidates(js)
        all_cands.extend(cands)
        if cands:
            break

    seen = set()
    dedup = []
    for c in all_cands:
        key = (c.get("symbol"), c.get("name"))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    return dedup


def quote_one(s: requests.Session, symbol: str) -> Optional[dict]:
    url = "https://stock.xueqiu.com/v5/stock/quote.json"
    js = req_json(s, url, {"symbol": symbol, "extend": "detail"})
    if not js:
        return None
    return ((js.get("data") or {}).get("quote") or None)


def choose_from_candidates(name: str, hint: str, cands: List[Dict[str, Any]]) -> Optional[str]:
    if not cands:
        return None

    hint_u = hint.upper()
    scored = []
    for c in cands:
        sym = normalize_symbol(c.get("symbol", ""))
        cname = clean_name(c.get("name", ""))
        score = 0
        if cname == name:
            score += 100
        elif name in cname or cname in name:
            score += 50
        if hint_u == "A" and sym.startswith(("SH", "SZ")):
            score += 40
        if hint_u == "HK" and sym.isdigit():
            score += 40
        if not hint and sym.startswith(("SH", "SZ")):
            score += 10
        scored.append((score, sym))

    scored.sort(reverse=True)
    return scored[0][1] if scored else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-search", action="store_true", help="本地映射找不到时尝试雪球搜索；默认不开，因为搜索接口经常403")
    parser.add_argument("--no-quote-verify", action="store_true", help="不验证 quote 接口")
    args = parser.parse_args()

    inputs = parse_input()
    s = build_session() if (args.use_search or not args.no_quote_verify) else None

    watch_rows = []
    cand_rows = []

    for item in inputs:
        name, theme, hint = item["input_name"], item["theme"], item["hint"]
        symbol = local_lookup(name, hint)
        source = "local_map"

        if not symbol and args.use_search and s is not None:
            cands = search_xueqiu(s, name)
            symbol = choose_from_candidates(name, hint, cands)
            source = "xueqiu_search"
            for c in cands:
                cand_rows.append({
                    "input_name": name,
                    "theme": theme,
                    "hint": hint,
                    "candidate_symbol": c.get("symbol"),
                    "candidate_name": c.get("name"),
                    "raw": c.get("raw"),
                })

        if not symbol:
            print(f"[WARN] 未找到：{name} hint={hint}")
            watch_rows.append({"symbol": "", "name": name, "theme": theme, "note": "NOT_FOUND", "source": ""})
            continue

        symbol = normalize_symbol(symbol)
        final_name = name

        if s is not None and not args.no_quote_verify:
            q = quote_one(s, symbol)
            if q and q.get("name"):
                final_name = q["name"]
            elif args.use_search:
                print(f"[WARN] quote验证失败，但保留本地代码：{name}->{symbol}")

        print(f"[OK] {name} -> {symbol} {final_name} source={source}")
        watch_rows.append({
            "symbol": symbol,
            "name": final_name,
            "theme": theme,
            "note": "OK",
            "source": source,
        })
        time.sleep(0.05)

    tag = now_tag()
    watch_path = OUT_DIR / f"watchlist_from_names_{tag}.csv"
    cand_path = OUT_DIR / f"name_candidates_{tag}.csv"

    out_df = pd.DataFrame(watch_rows)
    out_df[["symbol", "name", "theme"]].to_csv(watch_path, index=False, encoding="utf-8-sig")

    pd.DataFrame(cand_rows).to_csv(cand_path, index=False, encoding="utf-8-sig")

    print("\n完成：")
    print("1）可用 watchlist：", watch_path)
    print("2）候选审查表：", cand_path)
    print("\n确认无误后执行：")
    print(f'copy "{watch_path}" "{SCRIPT_DIR / DEFAULT_WATCHLIST_FILE}"')


if __name__ == "__main__":
    main()
