# -*- coding: utf-8 -*-
"""
02_ak_news_events_plus.py

News/event collector for the Streamlit "01" page.

Sources:
- AKShare: ak.stock_info_global_em(), stored directly as returned.
- Xueqiu: user timeline for https://xueqiu.com/u/5124430882, page-based.

Outputs are written to outputs_news/reports and outputs_news/logs so the
existing report center can continue reading them.
"""
from __future__ import annotations

import argparse
import hashlib
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
    import akshare as ak
except Exception as e:
    ak = None
    AK_IMPORT_ERROR = repr(e)
else:
    AK_IMPORT_ERROR = ""

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs_news" / "reports"
RAW_DIR = ROOT / "outputs_news" / "raw"
LOG_DIR = ROOT / "outputs_news" / "logs"
for d in [OUT_DIR, RAW_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

XUEQIU_USER_ID = "5124430882"
XUEQIU_USER_URL = f"https://xueqiu.com/u/{XUEQIU_USER_ID}"
XUEQIU_HOME = "https://xueqiu.com/"
COOKIE_FILE = ROOT / "xueqiu_cookie.txt"

TAG_RULES: Dict[str, List[str]] = {
    "FSD/智驾": ["FSD", "Robotaxi", "无人驾驶", "自动驾驶", "智驾", "智能驾驶", "特斯拉", "Tesla", "L3", "NOA", "OTA"],
    "H200/英伟达": ["H200", "英伟达", "NVIDIA", "黄仁勋", "Blackwell", "GB200", "GPU", "AI芯片"],
    "长鑫/存储": ["长鑫", "长鑫科技", "长鑫存储", "DRAM", "HBM", "存储", "美光", "Micron", "三星", "SK海力士"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "EDA", "封测", "中芯", "华虹", "北方华创", "中微公司"],
    "AI算力/CPO": ["CPO", "光模块", "光通信", "AI服务器", "算力", "数据中心", "液冷", "PCB", "交换机"],
    "消费电子": ["苹果", "Apple", "高通", "Qualcomm", "小米", "消费电子", "手机", "AI终端", "立讯", "蓝思", "舜宇"],
    "财报/业绩": ["财报", "业绩", "净利润", "营收", "指引", "预告", "盈喜", "亏损", "同比", "环比", "快报"],
    "控制权/并购": ["控制权", "实控人", "股权转让", "协议转让", "要约收购", "资产注入", "重大资产重组", "借壳", "战略投资", "收购"],
    "政策/监管": ["政策", "监管", "商务部", "发改委", "工信部", "证监会", "关税", "制裁", "出口管制", "审批"],
    "宏观/商品": ["美债", "美元", "人民币", "原油", "黄金", "降息", "CPI", "PPI", "非农", "通胀", "油价"],
    "港股/中概": ["港股", "恒生", "恒科", "中概", "ADR", "阿里", "腾讯", "美团", "京东", "百度"],
}

HIGH_WEIGHT_WORDS = [
    "获批", "批准", "上市", "上会", "注册", "生效", "发行", "申购", "涨停", "跌停", "大涨", "大跌",
    "超预期", "不及预期", "重大", "控制权", "实控人", "收购", "并购", "资产注入", "停牌", "复牌",
    "制裁", "关税", "出口管制", "财报", "业绩", "指引", "H200", "FSD", "长鑫", "英伟达", "特斯拉",
]

TITLE_CANDIDATES = ["标题", "新闻标题", "资讯标题", "内容标题", "title", "Title", "事件", "名称"]
SUMMARY_CANDIDATES = ["摘要", "内容", "简介", "正文", "description", "summary", "content", "Content"]
TIME_CANDIDATES = ["发布时间", "时间", "日期", "datetime", "time", "Time", "date", "Date", "pub_time", "showtime", "created_at"]
LINK_CANDIDATES = ["链接", "原文链接", "url", "URL", "link", "Link", "target"]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_str(x: Any) -> str:
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def clean_text(x: Any) -> str:
    s = html.unescape(safe_str(x))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_col(columns: List[str], candidates: List[str]) -> Optional[str]:
    lower_map = {str(c).lower(): c for c in columns}
    for cand in candidates:
        if cand in columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    for c in columns:
        cs = str(c).lower()
        for cand in candidates:
            if cand.lower() in cs:
                return c
    return None


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        v = float(value)
        try:
            if v > 10_000_000_000:
                return datetime.fromtimestamp(v / 1000)
            if v > 1_000_000_000:
                return datetime.fromtimestamp(v)
        except Exception:
            return None

    s = clean_text(value)
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", " ")
    s = re.sub(r"\s+", " ", s).strip()

    if "刚刚" in s:
        return datetime.now()
    m = re.match(r"(\d+)\s*分钟前", s)
    if m:
        return datetime.now() - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)\s*小时前", s)
    if m:
        return datetime.now() - timedelta(hours=int(m.group(1)))
    if s.startswith("今天 "):
        s = f"{datetime.now().strftime('%Y-%m-%d')} {s[3:]}"
    if s.startswith("昨天 "):
        d = datetime.now() - timedelta(days=1)
        s = f"{d.strftime('%Y-%m-%d')} {s[3:]}"
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
        s = f"{datetime.now().strftime('%Y-%m-%d')} {s}"

    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def tag_and_score(text: str) -> Tuple[str, int]:
    tags: List[str] = []
    score = 0
    upper_text = text.upper()
    for tag, words in TAG_RULES.items():
        if any(w.upper() in upper_text for w in words):
            tags.append(tag)
            score += 2
    for w in HIGH_WEIGHT_WORDS:
        if w.upper() in upper_text:
            score += 2
    if any(x in text for x in ["上市公司", "公告", "交易所", "证监会", "商务部", "工信部", "国务院"]):
        score += 1
    return ",".join(tags) if tags else "未分类", min(score, 20)


def make_id(source: str, title: str, time_str: str, link: str = "") -> str:
    raw = f"{source}|{title}|{time_str}|{link}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def row_fallback_title(row: pd.Series) -> str:
    parts: List[str] = []
    for _, v in row.items():
        sv = clean_text(v)
        if sv and sv.lower() not in {"nan", "none", "nat"} and len(sv) >= 4:
            parts.append(sv)
    return " | ".join(parts)[:260]


def normalize_table(df: pd.DataFrame, source: str, category: str, limit: Optional[int] = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    if limit:
        work = work.head(limit)

    cols = list(work.columns)
    title_col = find_col(cols, TITLE_CANDIDATES)
    summary_col = find_col(cols, SUMMARY_CANDIDATES)
    time_col = find_col(cols, TIME_CANDIDATES)
    link_col = find_col(cols, LINK_CANDIDATES)

    rows: List[Dict[str, Any]] = []
    for _, row in work.iterrows():
        title = clean_text(row.get(title_col, "")) if title_col else row_fallback_title(row)
        if not title:
            continue
        summary = clean_text(row.get(summary_col, "")) if summary_col else ""
        if not summary or summary == title:
            summary = row_fallback_title(row)
        dt = parse_time(row.get(time_col, "")) if time_col else None
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
        link = clean_text(row.get(link_col, "")) if link_col else ""
        tags, score = tag_and_score(f"{title} {summary}")
        rows.append({
            "id": make_id(source, title, time_str, link),
            "time": time_str,
            "source": source,
            "category": category,
            "title": title[:300],
            "summary": summary[:600],
            "tags": tags,
            "importance": score,
            "link": link,
        })
    return pd.DataFrame(rows)


def fetch_ak_em() -> Tuple[pd.DataFrame, pd.DataFrame]:
    logs: List[Dict[str, Any]] = []
    if ak is None:
        logs.append({"source": "akshare_em", "api": "stock_info_global_em", "status": "ERROR", "rows": 0, "normalized_rows": 0, "error": AK_IMPORT_ERROR})
        return pd.DataFrame(), pd.DataFrame(logs)

    api_name = "stock_info_global_em"
    try:
        df = ak.stock_info_global_em()
        raw_path = RAW_DIR / f"{now_tag()}_ak_stock_info_global_em.csv"
        df.to_csv(raw_path, index=False, encoding="utf-8-sig")
        logs.append({
            "source": "akshare_em",
            "api": api_name,
            "status": "OK",
            "rows": len(df),
            "normalized_rows": "",
            "raw_path": raw_path.as_posix(),
            "error": "",
        })
        return df, pd.DataFrame(logs)
    except Exception as e:
        logs.append({"source": "akshare_em", "api": api_name, "status": "ERROR", "rows": 0, "normalized_rows": 0, "raw_path": "", "error": repr(e)})
        return pd.DataFrame(), pd.DataFrame(logs)


def load_xueqiu_cookie() -> str:
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
    return " ".join(lines).strip().strip('"').strip("'")


def make_xueqiu_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": XUEQIU_USER_URL,
        "Origin": "https://xueqiu.com",
        "Connection": "keep-alive",
    })
    cookie = load_xueqiu_cookie()
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def request_json(session: requests.Session, url: str, params: Optional[dict] = None, retry: int = 1) -> Tuple[Optional[Any], str, int]:
    last_error = ""
    last_status = 0
    for i in range(retry + 1):
        try:
            r = session.get(url, params=params, timeout=15)
            last_status = r.status_code
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {(r.text or '')[:300]}", r.status_code
            text = (r.text or "").strip()
            if not text:
                return None, "empty body", r.status_code
            try:
                return r.json(), "", r.status_code
            except Exception:
                m = re.search(r"^[\w$]+\((.*)\)\s*;?$", text, re.S)
                if m:
                    return json.loads(m.group(1)), "", r.status_code
                return None, f"not json: {text[:300]}", r.status_code
        except Exception as e:
            last_error = repr(e)
            time.sleep(0.35 * (i + 1))
    return None, last_error, last_status


def iter_dicts(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_dicts(item)


def xueqiu_record_from_obj(obj: dict, source: str, user_id: str = XUEQIU_USER_ID) -> Optional[Dict[str, Any]]:
    text = clean_text(
        obj.get("title")
        or obj.get("description")
        or obj.get("text")
        or obj.get("content")
        or obj.get("summary")
        or ""
    )
    if len(text) < 6 or text.lower() in {"ok", "success"}:
        return None

    created = obj.get("created_at") or obj.get("createdAt") or obj.get("time") or obj.get("created") or obj.get("pub_time")
    dt = parse_time(created)
    time_str = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

    link = ""
    target = safe_str(obj.get("target"))
    if target:
        link = target if target.startswith("http") else "https://xueqiu.com" + target
    elif obj.get("url"):
        link = safe_str(obj.get("url"))
    elif obj.get("id"):
        link = f"https://xueqiu.com/{user_id}/{obj.get('id')}"

    tags, score = tag_and_score(text)
    return {
        "id": make_id(source, text, time_str, link),
        "time": time_str,
        "source": source,
        "category": "雪球7x24用户",
        "title": text[:300],
        "summary": text[:600],
        "tags": tags,
        "importance": score,
        "link": link,
    }


def fetch_xueqiu_user(user_id: str = XUEQIU_USER_ID, pages: int = 20, stop_hours: Optional[int] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    session = make_xueqiu_session()
    logs: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    cutoff = datetime.now() - timedelta(hours=stop_hours) if stop_hours else None

    try:
        r = session.get(XUEQIU_HOME, timeout=10)
        logs.append({"source": "xueqiu", "api": "home", "page": 0, "status": r.status_code, "rows": 0, "normalized_rows": 0, "error": ""})
    except Exception as e:
        logs.append({"source": "xueqiu", "api": "home", "page": 0, "status": "ERROR", "rows": 0, "normalized_rows": 0, "error": repr(e)})

    endpoints = [
        "https://xueqiu.com/statuses/user_timeline.json",
        "https://xueqiu.com/v4/statuses/user_timeline.json",
    ]

    for page in range(1, max(1, pages) + 1):
        got_this_page = 0
        last_error = ""
        for endpoint in endpoints:
            params = {"user_id": user_id, "page": page}
            js, err, status = request_json(session, endpoint, params=params, retry=1)
            if err or js is None:
                last_error = err
                logs.append({"source": "xueqiu", "api": endpoint, "page": page, "status": status or "ERROR", "rows": 0, "normalized_rows": 0, "error": err})
                continue

            raw_path = RAW_DIR / f"{now_tag()}_xueqiu_user_{user_id}_page{page}.json"
            raw_path.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")

            page_records: List[Dict[str, Any]] = []
            statuses = []
            if isinstance(js, dict):
                statuses = js.get("statuses") or js.get("list") or js.get("items") or []
            source_iter = statuses if statuses else list(iter_dicts(js))
            for obj in source_iter:
                if not isinstance(obj, dict):
                    continue
                rec = xueqiu_record_from_obj(obj, f"xueqiu_user:{user_id}", user_id=user_id)
                if rec:
                    page_records.append(rec)

            records.extend(page_records)
            got_this_page = len(page_records)
            page_times = [parse_time(x.get("time")) for x in page_records if x.get("time")]
            page_times = [x for x in page_times if x is not None]
            reached_cutoff = bool(cutoff and page_times and min(page_times) < cutoff)
            logs.append({
                "source": "xueqiu",
                "api": endpoint,
                "page": page,
                "status": status,
                "rows": len(statuses) if statuses else "",
                "normalized_rows": got_this_page,
                "oldest_time": min(page_times).strftime("%Y-%m-%d %H:%M:%S") if page_times else "",
                "reached_cutoff": reached_cutoff,
                "raw_path": raw_path.as_posix(),
                "error": "",
            })
            break

        if got_this_page == 0 and last_error:
            print(f"[xueqiu] page={page} failed: {last_error}")
        if cutoff and got_this_page and "reached_cutoff" in logs[-1] and logs[-1]["reached_cutoff"]:
            print(f"[xueqiu] page={page} reached requested time window, stop early.")
            break
        time.sleep(0.25)

    return pd.DataFrame(records), pd.DataFrame(logs)


def apply_filters(df: pd.DataFrame, hours: int, keywords: str, max_rows: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["id", "time", "source", "category", "title", "summary", "tags", "importance", "link"])
    work = df.copy()
    for col in ["title", "summary", "tags", "time", "source", "category", "link"]:
        if col not in work.columns:
            work[col] = ""

    work["title_norm"] = work["title"].astype(str).str.replace(r"\s+", "", regex=True)
    work = work.drop_duplicates(subset=["source", "title_norm"], keep="first").drop(columns=["title_norm"])

    cutoff = datetime.now() - timedelta(hours=max(1, int(hours)))

    def keep_time(s: Any) -> bool:
        dt = parse_time(s)
        if dt is None:
            return True
        return dt >= cutoff

    work = work[work["time"].apply(keep_time)]

    if keywords.strip():
        kws = [x.strip() for x in re.split(r"[,，;；\s]+", keywords) if x.strip()]
        if kws:
            pat = "|".join(re.escape(k) for k in kws)
            mask = (
                work["title"].str.contains(pat, case=False, na=False)
                | work["summary"].str.contains(pat, case=False, na=False)
                | work["tags"].str.contains(pat, case=False, na=False)
            )
            work = work[mask]

    work["_dt"] = work["time"].apply(lambda x: parse_time(x) or datetime.min)
    work["importance"] = pd.to_numeric(work["importance"], errors="coerce").fillna(0)
    work = work.sort_values(["_dt", "importance"], ascending=[False, False]).drop(columns=["_dt"])
    return work.head(max_rows).reset_index(drop=True)


def df_to_md_table(df: pd.DataFrame, max_rows: int = 120) -> str:
    if df is None or df.empty:
        return "缺数据"
    work = df.head(max_rows).fillna("").copy()
    cols = list(work.columns)

    def fmt(x: Any) -> str:
        return str(x).replace("\n", " ").replace("|", "/")[:600]

    lines = [
        "| " + " | ".join(fmt(c) for c in cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, r in work.iterrows():
        lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def save_outputs(df: pd.DataFrame, logs: pd.DataFrame, source: str, hours: int, pages: int) -> Dict[str, Path]:
    ts = now_tag()
    base = f"news_events_{source}_{ts}"
    csv_path = OUT_DIR / f"{base}.csv"
    json_path = OUT_DIR / f"{base}.json"
    md_path = OUT_DIR / f"{base}.md"
    log_path = LOG_DIR / f"{base}_log.csv"

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    logs.to_csv(log_path, index=False, encoding="utf-8-sig")

    show_cols = [c for c in ["time", "source", "category", "importance", "tags", "title", "summary", "link"] if c in df.columns]
    show_df = df[show_cols] if show_cols else df
    lines = [
        f"# 新闻事件包：{source} {ts}",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"时间窗口：最近 {hours} 小时" if source in {"xueqiu", "both"} else "AKShare：直接保存 ak.stock_info_global_em() 返回表",
        f"雪球页数：{pages}" if source in {"xueqiu", "both"} else "",
        f"总条数：{len(df)}",
        "",
        "## 1. 数据源日志",
        "",
        df_to_md_table(logs, max_rows=80),
        "",
        "## 2. 最新事件",
        "",
        df_to_md_table(show_df, max_rows=150) if not df.empty else "缺数据",
        "",
        "## 3. 给 ChatGPT 的固定问题",
        "",
        "读取本原始事件包，判断哪些事件可能改变盘前/盘中交易计划；说明影响对象、方向、强弱、持续时间，以及是否需要修改昨日计划或 prediction_log。不要把本文件当作交易结论。",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {"csv": csv_path, "json": json_path, "md": md_path, "log": log_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="AKShare / Xueqiu news event collector")
    parser.add_argument("--source", choices=["ak", "xueqiu", "both"], default="ak")
    parser.add_argument("--mode", default="manual", choices=["morning", "intraday", "manual", "all"], help="兼容旧按钮；只用于默认小时数")
    parser.add_argument("--hours", type=int, default=None)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--xueqiu-user-id", default=XUEQIU_USER_ID)
    parser.add_argument("--pages", type=int, default=20)
    args = parser.parse_args()

    hours = args.hours if args.hours is not None else {"morning": 12, "intraday": 2, "manual": 24, "all": 48}.get(args.mode, 24)

    print("=" * 70)
    print("新闻事件采集器")
    print(f"source={args.source} hours={hours} max={args.max} pages={args.pages}")
    print("=" * 70)

    frames: List[pd.DataFrame] = []
    log_frames: List[pd.DataFrame] = []

    if args.source in {"ak", "both"}:
        print("[AKShare] calling ak.stock_info_global_em()")
        df, logs = fetch_ak_em()
        frames.append(df)
        log_frames.append(logs)
        print(f"[AKShare] rows={len(df)}")

    if args.source in {"xueqiu", "both"}:
        print(f"[Xueqiu] user={args.xueqiu_user_id} pages={args.pages}")
        df, logs = fetch_xueqiu_user(user_id=args.xueqiu_user_id, pages=args.pages, stop_hours=int(hours))
        frames.append(df)
        log_frames.append(logs)
        print(f"[Xueqiu] normalized rows={len(df)}")

    all_df = pd.concat([x for x in frames if x is not None and not x.empty], ignore_index=True) if any(x is not None and not x.empty for x in frames) else pd.DataFrame()
    all_logs = pd.concat([x for x in log_frames if x is not None and not x.empty], ignore_index=True) if any(x is not None and not x.empty for x in log_frames) else pd.DataFrame()
    if args.source == "ak":
        out_df = all_df
    else:
        out_df = apply_filters(all_df, hours=int(hours), keywords=args.keywords, max_rows=int(args.max))

    paths = save_outputs(out_df, all_logs, args.source, int(hours), int(args.pages))

    print("\n[输出完成]")
    for k, p in paths.items():
        print(f"{k}: {p}")
    print("\n[前10条]")
    if out_df.empty:
        print("没有抓到数据。请看日志判断接口失败、Cookie 失效、页数不足，或时间/关键词过滤过窄。")
    else:
        for i, r in out_df.head(10).iterrows():
            print(f"{i + 1}. [{r.get('time', '')}] [{r.get('importance', '')}] {r.get('title', '')}")


if __name__ == "__main__":
    main()
