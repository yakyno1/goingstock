# -*- coding: utf-8 -*-
"""
02_ak_news_events_plus.py

最小新闻/事件采集器 v3：
- 复用 ak_news_oneclick 的思路：抓富途全球财经、财联社快讯，标准化输出。
- 增强：尝试抓公告、个股公告、巨潮公告、业绩报表/快报/预告/预约披露。
- 目标：输出“原始事件输入包”，不提前做买卖结论。

运行示例：
    python 02_ak_news_events_plus.py --mode morning --hours 12 --max 200
    python 02_ak_news_events_plus.py --mode intraday --hours 2 --keywords "工业富联,长鑫存储,AI算力"
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

try:
    import akshare as ak
except Exception as e:
    print("[ERROR] 未安装 akshare。请先运行 RUN_SETUP.bat")
    print(repr(e))
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs_news" / "reports"
RAW_DIR = ROOT / "outputs_news" / "raw"
LOG_DIR = ROOT / "outputs_news" / "logs"
for d in [OUT_DIR, RAW_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

TAG_RULES: Dict[str, List[str]] = {
    "FSD/智驾": ["FSD", "Robotaxi", "无人驾驶", "自动驾驶", "智驾", "智能驾驶", "特斯拉", "Tesla", "L3", "NOA", "OTA"],
    "H200/英伟达": ["H200", "英伟达", "NVIDIA", "黄仁勋", "Blackwell", "GB200", "GPU", "AI芯片"],
    "长鑫/存储": ["长鑫", "长鑫科技", "长鑫存储", "DRAM", "HBM", "存储", "美光", "Micron", "三星", "SK海力士"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "EDA", "封测", "中芯", "华虹", "北方华创", "中微公司"],
    "AI算力/CPO": ["CPO", "光模块", "光通信", "AI服务器", "算力", "数据中心", "液冷", "PCB", "交换机"],
    "消费电子": ["苹果", "Apple", "高通", "Qualcomm", "小米", "消费电子", "手机", "AI终端", "立讯", "蓝思", "舜宇"],
    "财报/业绩": ["财报", "业绩", "净利润", "营收", "指引", "预告", "盈喜", "亏损", "同比", "环比", "快报", "预约披露"],
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

TITLE_CANDIDATES = ["标题", "title", "Title", "新闻标题", "资讯标题", "内容标题", "公告标题", "事件", "名称"]
SUMMARY_CANDIDATES = ["摘要", "内容", "简介", "summary", "content", "Content", "description", "描述", "正文", "公告内容"]
TIME_CANDIDATES = ["时间", "发布时间", "日期", "datetime", "time", "Time", "date", "Date", "pub_time", "showtime", "公告日期", "预约披露日期"]
LINK_CANDIDATES = ["链接", "url", "URL", "link", "Link", "原文链接", "公告链接"]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_str(x: Any) -> str:
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", safe_str(text)).strip()


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
    s = safe_str(value)
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", " ")
    s = re.sub(r"\s+", " ", s).strip()
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
    if any(x in text for x in ["上市公司", "公告", "交易所", "证监会", "商务部", "工信部"]):
        score += 1
    return ",".join(tags) if tags else "未分类", min(score, 20)


def row_fallback_title(row: pd.Series) -> str:
    parts: List[str] = []
    for _, v in row.items():
        sv = clean_text(v)
        if sv and sv.lower() not in {"nan", "none", "nat"} and len(sv) >= 4:
            parts.append(sv)
    return " | ".join(parts)[:200]


def row_to_summary(row: pd.Series, summary_col: Optional[str], title: str) -> str:
    if summary_col:
        text = clean_text(row.get(summary_col, ""))
        if text and text != title:
            return text[:300]
    parts: List[str] = []
    for c, v in row.items():
        sv = clean_text(v)
        if not sv or sv == title or sv.lower() in {"nan", "none", "nat"}:
            continue
        if len(sv) > 8:
            parts.append(sv)
    return "；".join(parts)[:300]


def normalize_df(df: pd.DataFrame, source: str, category: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)
    title_col = find_col(cols, TITLE_CANDIDATES)
    summary_col = find_col(cols, SUMMARY_CANDIDATES)
    time_col = find_col(cols, TIME_CANDIDATES)
    link_col = find_col(cols, LINK_CANDIDATES)

    if title_col is None:
        for c in cols:
            try:
                sample = df[c].dropna().astype(str).head(10).tolist()
                if sample and max(len(x) for x in sample) >= 6:
                    title_col = c
                    break
            except Exception:
                pass

    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        title = clean_text(row.get(title_col, "")) if title_col else row_fallback_title(row)
        if not title:
            continue
        summary = row_to_summary(row, summary_col, title)
        dt = parse_time(row.get(time_col, "")) if time_col else None
        tstr = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
        link = safe_str(row.get(link_col, "")) if link_col else ""
        text_for_tag = f"{title} {summary}"
        tags, score = tag_and_score(text_for_tag)
        news_id = hashlib.md5(f"{source}|{category}|{title}|{tstr}".encode("utf-8")).hexdigest()[:16]
        records.append({
            "id": news_id,
            "time": tstr,
            "source": source,
            "category": category,
            "title": title,
            "summary": summary,
            "tags": tags,
            "importance": score,
            "link": link,
        })
    return pd.DataFrame(records)


def safe_ak_call(name: str, func_name: str, category: str, variants: List[Dict[str, Any]]) -> Tuple[List[pd.DataFrame], List[Dict[str, Any]]]:
    out: List[pd.DataFrame] = []
    logs: List[Dict[str, Any]] = []
    if not hasattr(ak, func_name):
        logs.append({"source": name, "api": func_name, "status": "MISSING", "rows": 0, "error": "akshare has no function"})
        return out, logs
    func = getattr(ak, func_name)
    for kwargs in variants:
        tag = f"{name}_{hashlib.md5(json.dumps(kwargs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:6]}"
        try:
            df = func(**kwargs) if kwargs else func()
            raw_path = RAW_DIR / f"{now_tag()}_{tag}.csv"
            try:
                df.to_csv(raw_path, index=False, encoding="utf-8-sig")
            except Exception:
                pass
            nd = normalize_df(df, name, category)
            logs.append({"source": name, "api": func_name, "kwargs": json.dumps(kwargs, ensure_ascii=False), "status": "OK", "rows": len(df), "normalized_rows": len(nd), "error": ""})
            if not nd.empty:
                out.append(nd)
            break
        except TypeError as e:
            logs.append({"source": name, "api": func_name, "kwargs": json.dumps(kwargs, ensure_ascii=False), "status": "TYPE_ERROR", "rows": 0, "normalized_rows": 0, "error": repr(e)})
            continue
        except Exception as e:
            logs.append({"source": name, "api": func_name, "kwargs": json.dumps(kwargs, ensure_ascii=False), "status": "ERROR", "rows": 0, "normalized_rows": 0, "error": repr(e)})
            time.sleep(0.3)
            continue
    return out, logs


def build_call_plan() -> List[Tuple[str, str, str, List[Dict[str, Any]]]]:
    # 多参数兜底：AkShare 版本差异较大，失败写 log，不让程序中断。
    return [
        ("ak_futu_global", "stock_info_global_futu", "新闻快讯", [{}]),
        ("ak_cls_all", "stock_info_global_cls", "新闻快讯", [{"symbol": "全部"}, {}]),
        ("ak_notice_report", "stock_notice_report", "公告", [{"symbol": "全部"}, {}]),
        ("ak_cninfo_disclosure", "stock_zh_a_disclosure_report_cninfo", "公告", [{}, {"symbol": "全部"}]),
        ("ak_yjbb", "stock_yjbb_em", "财报/业绩", [{}, {"date": "20240331"}, {"date": "20240630"}, {"date": "20240930"}, {"date": "20241231"}]),
        ("ak_yjkb", "stock_yjkb_em", "财报/业绩", [{}, {"date": "20240331"}, {"date": "20240630"}, {"date": "20240930"}, {"date": "20241231"}]),
        ("ak_yjyg", "stock_yjyg_em", "财报/业绩", [{}, {"date": "20240331"}, {"date": "20240630"}, {"date": "20240930"}, {"date": "20241231"}]),
        ("ak_yysj", "stock_yysj_em", "预约披露", [{}, {"date": "20240331"}, {"date": "20240630"}, {"date": "20240930"}, {"date": "20241231"}]),
    ]


def fetch_all() -> Tuple[pd.DataFrame, pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    log_rows: List[Dict[str, Any]] = []
    for name, func_name, category, variants in build_call_plan():
        print(f"[抓取] {name} / {func_name}")
        fs, logs = safe_ak_call(name, func_name, category, variants)
        frames.extend(fs)
        log_rows.extend(logs)
    if frames:
        all_df = pd.concat(frames, ignore_index=True)
    else:
        all_df = pd.DataFrame(columns=["id", "time", "source", "category", "title", "summary", "tags", "importance", "link"])
    log_df = pd.DataFrame(log_rows)
    return all_df, log_df


def filter_events(df: pd.DataFrame, hours: int, keywords: str, max_rows: int) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["title_norm"] = df["title"].astype(str).str.replace(r"\s+", "", regex=True)
    df = df.drop_duplicates(subset=["title_norm"], keep="first").drop(columns=["title_norm"])

    cutoff = datetime.now() - timedelta(hours=hours)
    def keep_time(s: str) -> bool:
        dt = parse_time(s)
        if dt is None:
            return True
        return dt >= cutoff
    df = df[df["time"].apply(keep_time)]

    if keywords.strip():
        kws = [x.strip() for x in re.split(r"[,，;；\s]+", keywords) if x.strip()]
        if kws:
            pat = "|".join(re.escape(k) for k in kws)
            mask = (
                df["title"].str.contains(pat, case=False, na=False)
                | df["summary"].str.contains(pat, case=False, na=False)
                | df["tags"].str.contains(pat, case=False, na=False)
            )
            df = df[mask]

    df["_dt"] = df["time"].apply(lambda x: parse_time(x) or datetime.min)
    df = df.sort_values(["importance", "_dt"], ascending=[False, False]).drop(columns=["_dt"])
    return df.head(max_rows).reset_index(drop=True)


def df_to_md_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df is None or df.empty:
        return "缺数据"
    work = df.head(max_rows).fillna("").copy()
    cols = list(work.columns)
    def fmt(x: Any) -> str:
        return str(x).replace("\n", " ").replace("|", "/")[:500]
    lines = ["| " + " | ".join(map(fmt, cols)) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, r in work.iterrows():
        lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def save_outputs(df: pd.DataFrame, log_df: pd.DataFrame, mode: str) -> Dict[str, Path]:
    ts = now_tag()
    base = f"news_events_plus_{mode}_{ts}"
    csv_path = OUT_DIR / f"{base}.csv"
    json_path = OUT_DIR / f"{base}.json"
    md_path = OUT_DIR / f"{base}.md"
    log_path = LOG_DIR / f"{base}_log.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    log_df.to_csv(log_path, index=False, encoding="utf-8-sig")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 新闻/财报/公告原始事件包：{mode} {ts}\n\n")
        f.write(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"总条数：{len(df)}\n\n")
        f.write("## 1. 数据源日志\n\n")
        f.write(df_to_md_table(log_df, max_rows=50))
        f.write("\n\n## 2. 高权重事件\n\n")
        show_cols = [c for c in ["time", "source", "category", "title", "summary", "tags", "importance", "link"] if c in df.columns]
        f.write(df_to_md_table(df[show_cols], max_rows=100) if not df.empty else "缺数据")
        f.write("\n\n## 3. 给 ChatGPT 的固定指令\n\n")
        f.write("读取本原始事件包，按盘前事件雷达/盘中补雷达判断：哪些事件可能导致大涨/大跌；影响对象；预计影响幅度；持续时间；是否修改昨日计划或 prediction_log。不要把本文件当作交易结论。\n")
    return {"csv": csv_path, "json": json_path, "md": md_path, "log": log_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="AKShare 新闻/财报/公告增强采集器")
    parser.add_argument("--mode", default="morning", choices=["morning", "intraday", "manual", "all"], help="运行模式")
    parser.add_argument("--hours", type=int, default=None, help="最近多少小时")
    parser.add_argument("--max", type=int, default=200, help="最多输出多少条")
    parser.add_argument("--keywords", default="", help="关键词，逗号分隔")
    args = parser.parse_args()

    hours = args.hours if args.hours is not None else {"morning": 12, "intraday": 2, "manual": 24, "all": 48}.get(args.mode, 12)
    print("=" * 70)
    print("AKShare 新闻/财报/公告增强采集器")
    print(f"模式: {args.mode} | 最近小时: {hours} | 关键词: {args.keywords or '无'}")
    print("=" * 70)

    all_df, log_df = fetch_all()
    out_df = filter_events(all_df, hours=hours, keywords=args.keywords, max_rows=args.max)
    paths = save_outputs(out_df, log_df, args.mode)
    print("\n[输出完成]")
    for k, p in paths.items():
        print(f"{k.upper()}: {p}")
    print("\n[前10条]")
    if out_df.empty:
        print("没有抓到数据。看 log 文件判断是接口失败还是关键词过窄。")
    else:
        for i, r in out_df.head(10).iterrows():
            print(f"{i+1}. [{r['importance']}] {r['title']} | {r['category']} | {r['tags']}")


if __name__ == "__main__":
    main()
