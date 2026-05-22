#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_xueqiu_live_news_collect.py

雪球快讯/盘前快讯采集器
定位：替代慢的全量新闻源，优先抓雪球 7x24/快讯/搜索结果，输出原始输入包给 GPT。

特性：
1）读取 xueqiu_cookie.txt；
2）先访问雪球首页刷新会话；
3）优先尝试 Xueqiu livenews 接口；
4）可按关键词搜索帖子；
5）可按 user_id 抓指定账号时间线；
6）按小时窗口过滤，输出 csv/json/md/log；
7）只做原始数据包，不给交易结论。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "outputs_xueqiu_news"
RAW_DIR = OUT_ROOT / "raw"
NORM_DIR = OUT_ROOT / "normalized"
REPORT_DIR = OUT_ROOT / "reports"
LOG_DIR = OUT_ROOT / "logs"
for d in [RAW_DIR, NORM_DIR, REPORT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

XUEQIU_HOME = "https://xueqiu.com/"
XUEQIU_BASE = "https://xueqiu.com"
COOKIE_FILE = ROOT / "xueqiu_cookie.txt"

TAG_RULES: Dict[str, List[str]] = {
    "盘前/隔夜": ["盘前", "隔夜", "早报", "开盘前", "美股", "外盘", "期指", "ADR"],
    "AI算力/CPO": ["AI", "算力", "CPO", "光模块", "AI服务器", "数据中心", "液冷", "交换机", "PCB"],
    "英伟达/H200": ["英伟达", "NVIDIA", "黄仁勋", "H200", "GB200", "Blackwell", "GPU"],
    "半导体/存储": ["半导体", "芯片", "晶圆", "HBM", "DRAM", "存储", "美光", "海力士", "三星", "长鑫"],
    "财报/业绩": ["财报", "业绩", "净利润", "营收", "指引", "预告", "盈喜", "亏损", "同比", "环比"],
    "政策/监管": ["政策", "监管", "证监会", "交易所", "工信部", "发改委", "商务部", "关税", "制裁", "出口管制"],
    "汽车/智驾": ["汽车", "智驾", "自动驾驶", "FSD", "Robotaxi", "特斯拉", "小米汽车", "理想", "蔚来", "小鹏"],
    "港股/中概": ["港股", "恒生", "恒科", "中概", "阿里", "腾讯", "美团", "京东", "百度", "拼多多", "B站"],
    "商品/宏观": ["美元", "人民币", "美债", "黄金", "原油", "CPI", "PPI", "非农", "降息", "通胀"],
}

HIGH_WEIGHT_WORDS = [
    "超预期", "不及预期", "重大", "获批", "批准", "上市", "上会", "注册", "生效", "发行", "申购",
    "涨停", "跌停", "大涨", "大跌", "暴涨", "暴跌", "停牌", "复牌", "收购", "并购", "资产注入",
    "制裁", "关税", "出口管制", "财报", "业绩", "指引", "H200", "FSD", "长鑫", "英伟达", "特斯拉",
]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_text(x: Any) -> str:
    if x is None:
        return ""
    s = str(x)
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_cookie() -> str:
    env = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env:
        return env.strip().strip('"').strip("'")
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
    text = " ".join(lines).strip().strip('"').strip("'")
    if "这里粘贴" in text or "你的完整雪球Cookie" in text:
        return ""
    return text


def build_session() -> requests.Session:
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
        "Connection": "keep-alive",
    })
    cookie = load_cookie()
    if cookie:
        s.headers["Cookie"] = cookie
        print(f"[INFO] 已读取雪球Cookie，长度={len(cookie)}，xq_a_token={'xq_a_token=' in cookie}")
    else:
        print("[WARN] 未读取到雪球Cookie；部分接口可能失败。")
    try:
        r = s.get(XUEQIU_HOME, timeout=10)
        print(f"[INFO] 雪球首页状态码：{r.status_code}")
    except Exception as e:
        print(f"[WARN] 访问雪球首页失败：{e}")
    return s


def request_json(session: requests.Session, url: str, params: Optional[dict] = None, retry: int = 1) -> Tuple[Optional[Any], Optional[str]]:
    last_err = None
    for i in range(retry + 1):
        try:
            r = session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {(r.text or '')[:200]}"
            text = (r.text or "").strip()
            if not text:
                return None, "empty body"
            # Some endpoints may return JSONP-like text. Try normal JSON first.
            try:
                return r.json(), None
            except Exception:
                m = re.search(r"^[\w$]+\((.*)\)\s*;?$", text, re.S)
                if m:
                    return json.loads(m.group(1)), None
                return None, f"not json: {text[:200]}"
        except Exception as e:
            last_err = repr(e)
            time.sleep(0.4 * (i + 1))
    return None, last_err


def parse_time(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        # Xueqiu timestamps are usually ms, sometimes seconds.
        v = float(x)
        try:
            if v > 10_000_000_000:
                return datetime.fromtimestamp(v / 1000)
            if v > 1_000_000_000:
                return datetime.fromtimestamp(v)
        except Exception:
            return None
    s = clean_text(x)
    if not s:
        return None
    # 3小时前 / 5分钟前
    m = re.match(r"(\d+)\s*分钟前", s)
    if m:
        return datetime.now() - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)\s*小时前", s)
    if m:
        return datetime.now() - timedelta(hours=int(m.group(1)))
    if "刚刚" in s:
        return datetime.now()
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def tag_and_score(text: str) -> Tuple[str, int]:
    tags = []
    score = 0
    u = text.upper()
    for tag, words in TAG_RULES.items():
        if any(w.upper() in u for w in words):
            tags.append(tag)
            score += 2
    for w in HIGH_WEIGHT_WORDS:
        if w.upper() in u:
            score += 2
    if any(x in text for x in ["公告", "交易所", "证监会", "工信部", "商务部", "国务院"]):
        score += 1
    return ",".join(tags) if tags else "未分类", min(score, 20)


def iter_dicts(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_dicts(item)


def record_from_obj(obj: dict, source: str) -> Optional[dict]:
    # Try common Xueqiu status / live news fields.
    title = clean_text(obj.get("title") or obj.get("description") or obj.get("text") or obj.get("content") or obj.get("summary") or "")
    if not title or len(title) < 6:
        return None
    # Filter pure HTML fragments / too generic objects.
    if title in {"OK", "success"}:
        return None

    created = obj.get("created_at") or obj.get("createdAt") or obj.get("time") or obj.get("created") or obj.get("pub_time")
    dt = parse_time(created)
    time_str = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
    rid = obj.get("id") or obj.get("status_id") or obj.get("target") or obj.get("url") or ""
    url = ""
    if obj.get("target"):
        target = str(obj.get("target"))
        url = target if target.startswith("http") else "https://xueqiu.com" + target
    elif obj.get("url"):
        url = str(obj.get("url"))

    tags, score = tag_and_score(title)
    return {
        "time": time_str,
        "source": source,
        "title": title[:300],
        "summary": title[:500],
        "tags": tags,
        "importance": score,
        "url": url,
        "raw_id": str(rid),
    }


def fetch_livenews(session: requests.Session, count: int) -> Tuple[pd.DataFrame, List[dict]]:
    logs = []
    urls = [
        "https://xueqiu.com/statuses/livenews/list.json",
        "https://xueqiu.com/statuses/livenews/list.json",
    ]
    param_list = [
        {"since_id": -1, "max_id": -1, "count": count},
        {"since_id": "", "max_id": "", "count": count},
    ]
    records = []
    for url, params in zip(urls, param_list):
        js, err = request_json(session, url, params=params, retry=1)
        logs.append({"stage": "livenews", "url": url, "status": "ERROR" if err else "OK", "error": err or ""})
        if err or js is None:
            continue
        raw_path = RAW_DIR / f"xueqiu_livenews_{now_tag()}.json"
        raw_path.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
        for obj in iter_dicts(js):
            rec = record_from_obj(obj, "xueqiu_livenews")
            if rec:
                records.append(rec)
        if records:
            break
    return pd.DataFrame(records), logs


def fetch_search(session: requests.Session, queries: List[str], max_per_query: int) -> Tuple[pd.DataFrame, List[dict]]:
    records = []
    logs = []
    endpoints = [
        "https://xueqiu.com/query/v1/search/status.json",
        "https://xueqiu.com/statuses/search.json",
    ]
    for q in queries:
        q = q.strip()
        if not q:
            continue
        for ep in endpoints:
            params = {"q": q, "count": max_per_query, "page": 1, "sortId": 1}
            js, err = request_json(session, ep, params=params, retry=0)
            logs.append({"stage": "search", "query": q, "url": ep, "status": "ERROR" if err else "OK", "error": err or ""})
            if err or js is None:
                continue
            safe_q = re.sub(r"[^\\w\\u4e00-\\u9fff]+", "_", str(q)).strip("_") or "keyword"
            raw_path = RAW_DIR / f"xueqiu_search_{safe_q}_{now_tag()}.json"
            raw_path.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
            got = 0
            for obj in iter_dicts(js):
                rec = record_from_obj(obj, f"xueqiu_search:{q}")
                if rec:
                    records.append(rec)
                    got += 1
                    if got >= max_per_query:
                        break
            if got:
                break
            time.sleep(0.2)
    return pd.DataFrame(records), logs


def fetch_user_timeline(session: requests.Session, user_id: str, pages: int = 2) -> Tuple[pd.DataFrame, List[dict]]:
    records = []
    logs = []
    if not user_id:
        return pd.DataFrame(), logs
    url = "https://xueqiu.com/statuses/user_timeline.json"
    for page in range(1, pages + 1):
        params = {"user_id": user_id, "page": page}
        js, err = request_json(session, url, params=params, retry=1)
        logs.append({"stage": "timeline", "user_id": user_id, "page": page, "status": "ERROR" if err else "OK", "error": err or ""})
        if err or js is None:
            continue
        raw_path = RAW_DIR / f"xueqiu_timeline_{user_id}_{page}_{now_tag()}.json"
        raw_path.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
        for obj in iter_dicts(js):
            rec = record_from_obj(obj, f"xueqiu_timeline:{user_id}")
            if rec:
                records.append(rec)
        time.sleep(0.25)
    return pd.DataFrame(records), logs


def filter_df(df: pd.DataFrame, hours: int, keywords: str, max_rows: int) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["title_norm"] = df["title"].astype(str).str.replace(r"\s+", "", regex=True)
    df = df.drop_duplicates(subset=["title_norm"], keep="first").drop(columns=["title_norm"])
    cutoff = datetime.now() - timedelta(hours=hours)

    def keep_time(s: str) -> bool:
        dt = parse_time(s)
        if dt is None:
            # keep unknown-time items; some Xueqiu endpoints do not expose time cleanly.
            return True
        return dt >= cutoff

    df = df[df["time"].apply(keep_time)]
    if keywords.strip():
        kws = [x.strip() for x in re.split(r"[,，;；\s]+", keywords) if x.strip()]
        if kws:
            pat = "|".join(re.escape(k) for k in kws)
            df = df[
                df["title"].str.contains(pat, case=False, na=False)
                | df["summary"].str.contains(pat, case=False, na=False)
                | df["tags"].str.contains(pat, case=False, na=False)
            ]
    df["_dt"] = df["time"].apply(lambda x: parse_time(x) or datetime.min)
    df = df.sort_values(["importance", "_dt"], ascending=[False, False]).drop(columns=["_dt"])
    return df.head(max_rows).reset_index(drop=True)


def build_report(df: pd.DataFrame, logs_df: pd.DataFrame, mode: str, hours: int) -> str:
    lines = []
    lines.append(f"# 雪球快讯/盘前快讯原始输入包 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"模式：{mode}；时间窗口：最近 {hours} 小时")
    lines.append("")
    lines.append("说明：本文件只提供雪球快讯/帖子原始数据和机器标签，不提供交易结论。")
    lines.append("")
    lines.append("## 1. 接口日志")
    lines.append("")
    if logs_df.empty:
        lines.append("缺日志")
    else:
        lines.append(df_to_md(logs_df.head(80)))
    lines.append("")
    lines.append("## 2. 盘前/隔夜/合集类快讯")
    lines.append("")
    if df.empty:
        lines.append("缺数据")
    else:
        prem = df[df["title"].str.contains("盘前|隔夜|早报|开盘前|美股|外盘|ADR", case=False, na=False)].copy()
        if prem.empty:
            lines.append("缺数据")
        else:
            lines.append(df_to_md(prem[["time", "source", "importance", "tags", "title", "url"]].head(80)))
    lines.append("")
    lines.append("## 3. 高权重快讯")
    lines.append("")
    if df.empty:
        lines.append("缺数据")
    else:
        lines.append(df_to_md(df[["time", "source", "importance", "tags", "title", "url"]].head(120)))
    lines.append("")
    lines.append("## 4. 给 GPT 的固定问题")
    lines.append("1. 哪些快讯足以改变盘前/盘中交易计划？")
    lines.append("2. 哪些是大涨/大跌变量，影响对象是谁？")
    lines.append("3. 哪些新闻只是噪音，不能影响买卖？")
    lines.append("4. 是否需要修正 AI算力、半导体、汽车、港股互联网等方向？")
    return "\n".join(lines)


def df_to_md(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "缺数据"
    work = df.fillna("").copy()
    cols = list(work.columns)
    def fmt(x: Any) -> str:
        return str(x).replace("|", "/").replace("\n", " ")[:500]
    header = "| " + " | ".join(fmt(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = []
    for _, r in work.iterrows():
        rows.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join([header, sep] + rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="雪球快讯/盘前快讯采集器")
    parser.add_argument("--mode", choices=["livenews", "search", "timeline", "all"], default="all")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--keywords", default="", help="输出过滤关键词，空=不过滤")
    parser.add_argument("--queries", default="7X24快讯,盘前快讯,隔夜,美股,英伟达,财报,长鑫,H200,FSD,AI算力,半导体", help="雪球搜索关键词，逗号分隔")
    parser.add_argument("--user-id", default="", help="可选：指定雪球用户ID抓时间线")
    parser.add_argument("--pages", type=int, default=2)
    args = parser.parse_args()

    print("=" * 70)
    print("雪球快讯/盘前快讯采集")
    print(f"mode={args.mode} hours={args.hours} max={args.max}")
    print("=" * 70)

    session = build_session()
    frames = []
    logs = []

    if args.mode in {"livenews", "all"}:
        df, lg = fetch_livenews(session, count=min(max(args.max, 50), 200))
        frames.append(df)
        logs.extend(lg)
        print(f"[livenews] rows={len(df)}")

    if args.mode in {"search", "all"}:
        queries = [x.strip() for x in re.split(r"[,，;；]", args.queries) if x.strip()]
        df, lg = fetch_search(session, queries, max_per_query=30)
        frames.append(df)
        logs.extend(lg)
        print(f"[search] rows={len(df)} queries={len(queries)}")

    if args.mode in {"timeline", "all"} and args.user_id.strip():
        df, lg = fetch_user_timeline(session, args.user_id.strip(), pages=args.pages)
        frames.append(df)
        logs.extend(lg)
        print(f"[timeline] rows={len(df)}")

    if frames:
        all_df = pd.concat([x for x in frames if x is not None and not x.empty], ignore_index=True) if any(not x.empty for x in frames) else pd.DataFrame()
    else:
        all_df = pd.DataFrame()

    if all_df.empty:
        out_df = pd.DataFrame(columns=["time", "source", "title", "summary", "tags", "importance", "url", "raw_id"])
    else:
        out_df = filter_df(all_df, args.hours, args.keywords, args.max)

    logs_df = pd.DataFrame(logs)
    tag = now_tag()
    csv_path = NORM_DIR / f"xueqiu_live_news_{args.mode}_{tag}.csv"
    json_path = NORM_DIR / f"xueqiu_live_news_{args.mode}_{tag}.json"
    md_path = REPORT_DIR / f"xueqiu_live_news_packet_{args.mode}_{tag}.md"
    log_path = LOG_DIR / f"xueqiu_live_news_log_{args.mode}_{tag}.csv"

    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    out_df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    logs_df.to_csv(log_path, index=False, encoding="utf-8-sig")
    md_path.write_text(build_report(out_df, logs_df, args.mode, args.hours), encoding="utf-8")

    print("\n[输出完成]")
    print("csv:", csv_path)
    print("json:", json_path)
    print("md:", md_path)
    print("log:", log_path)
    print("\n[前10条]")
    if out_df.empty:
        print("缺数据。看 log 判断接口失败还是关键词过滤过窄。")
    else:
        for i, r in out_df.head(10).iterrows():
            print(f"{i+1}. [{r['importance']}] {r['title']} | {r['tags']}")


if __name__ == "__main__":
    main()
