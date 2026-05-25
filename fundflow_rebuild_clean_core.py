# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, List, Dict, Tuple
from http.cookies import SimpleCookie
import re
import sys
import time
import subprocess
import webbrowser
import json

import pandas as pd


CLEAN_DIR_NAME = "outputs_fundflow_clean_v2"
CACHE_FILE = "fundflow_long_cache.csv"
QUALITY_FILE = "fundflow_quality.csv"
TRADE_CAL_FILE = "trade_dates_a.csv"


# =========================
# 基础工具
# =========================

def project_root_from_page(page_file: str | Path) -> Path:
    p = Path(page_file).resolve()
    return p.parents[1] if p.parent.name == "pages" else p.parent


def clean_root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve() / CLEAN_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    (root / "cache").mkdir(exist_ok=True)
    (root / "exports").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    return root


def cache_path(project_root: str | Path) -> Path:
    return clean_root(project_root) / "cache" / CACHE_FILE


def quality_path(project_root: str | Path) -> Path:
    return clean_root(project_root) / "cache" / QUALITY_FILE


def resume_state_path(project_root: str | Path) -> Path:
    return clean_root(project_root) / "cache" / "repair_resume_state.json"


def trade_calendar_path(project_root: str | Path) -> Path:
    return clean_root(project_root) / "cache" / TRADE_CAL_FILE


def read_csv_safe(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(p, dtype=str, encoding=enc)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
        except Exception:
            pass

    try:
        return pd.read_csv(p, dtype=str, encoding="utf-8-sig", engine="python", on_bad_lines="skip")
    except Exception:
        return pd.DataFrame()


def is_bad_cell(x) -> bool:
    if x is None:
        return True
    s = str(x).strip()
    return s in ("", "None", "nan", "NaN", "-", "--")


def normalize_date(s) -> str:
    if isinstance(s, (datetime, date)):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def compact_date(s) -> str:
    return normalize_date(s).replace("-", "")


def business_days(start: str, end: str) -> List[str]:
    s = pd.to_datetime(normalize_date(start)).date()
    e = pd.to_datetime(normalize_date(end)).date()
    out = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _calendar_dates_from_df(df: pd.DataFrame) -> List[str]:
    if df is None or df.empty:
        return []
    for c in ("trade_date", "date", "cal_date"):
        if c in df.columns:
            col = c
            break
    else:
        col = df.columns[0] if len(df.columns) else ""
    if not col:
        return []
    ds = pd.to_datetime(df[col], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist()
    return sorted(set(ds))


def read_trade_calendar(project_root: str | Path) -> List[str]:
    p = trade_calendar_path(project_root)
    if not p.exists():
        return []
    return _calendar_dates_from_df(read_csv_safe(p))


def save_trade_calendar(project_root: str | Path, dates: List[str]) -> None:
    if not dates:
        return
    p = trade_calendar_path(project_root)
    pd.DataFrame({"trade_date": sorted(set(dates))}).to_csv(p, index=False, encoding="utf-8-sig")


def fetch_trade_calendar() -> List[str]:
    try:
        import akshare as ak  # lazy import; may require network
        df = ak.tool_trade_date_hist_sina()
        return _calendar_dates_from_df(df)
    except Exception:
        return []


def trading_days(start: str, end: str, project_root: Optional[str | Path] = None) -> List[str]:
    s = normalize_date(start)
    e = normalize_date(end)
    try:
        if pd.to_datetime(s) > pd.to_datetime(e):
            s, e = e, s
    except Exception:
        return []

    fallback = business_days(s, e)
    if project_root is None:
        return fallback

    cache_dates = read_trade_calendar(project_root)
    if cache_dates:
        in_range = [d for d in cache_dates if s <= d <= e]
        if in_range:
            return in_range

    fetched = fetch_trade_calendar()
    if fetched:
        save_trade_calendar(project_root, fetched)
        in_range = [d for d in fetched if s <= d <= e]
        if in_range:
            return in_range

    if cache_dates:
        return [d for d in cache_dates if s <= d <= e]
    return fallback


def detect_kind_from_name(path: Path) -> str:
    name = path.name.lower()
    if "concept" in name or "概念" in path.name:
        return "concept"
    if "industry" in name or "行业" in path.name:
        return "industry"
    return "unknown"


def extract_amount(cell: str) -> Optional[float]:
    if is_bad_cell(cell):
        return None
    s = str(cell).replace("，", ",")
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*亿", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    m = re.search(r"[（(]\s*([+-]?\d+(?:\.\d+)?)", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def extract_board_name(cell: str) -> str:
    if is_bad_cell(cell):
        return ""
    return re.split(r"[（(]", str(cell).strip())[0].strip()


def rank_info(label: str) -> Tuple[str, int]:
    s = str(label).strip()
    direction = "inflow" if s.startswith("前") else ("outflow" if s.startswith("后") else "unknown")
    m = re.search(r"\d+", s)
    rn = int(m.group(0)) if m else 999
    return direction, rn


def find_rank_col(df: pd.DataFrame) -> Optional[str]:
    if df.empty:
        return None
    for c in df.columns:
        vals = df[c].astype(str).head(30).tolist()
        if str(c) in ("排名", "rank", "Rank") or any(v.startswith("前") or v.startswith("后") for v in vals):
            return c
    return df.columns[0] if len(df.columns) else None


def is_date_col(c: str) -> bool:
    s = str(c)
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) or re.fullmatch(r"\d{8}", s))


def get_date_cols(df: pd.DataFrame, rank_col: str) -> List[str]:
    ds = [c for c in df.columns if c != rank_col and is_date_col(str(c))]
    return ds


# =========================
# 扫描与解析旧输出
# =========================

def discover_source_csvs(project_root: str | Path) -> pd.DataFrame:
    root = Path(project_root).resolve()
    patterns = [
        "outputs_fundflow*",
        "outputs_market_fundflow*",
        "outputs_market_fundflow_cookie*",
        "outputs_combined*",
        "outputs_fundflow_cookie_clean*",
    ]
    files = []
    for pat in patterns:
        for d in root.glob(pat):
            if d.exists():
                files.extend(d.rglob("*.csv"))
    files.extend(root.glob("*fundflow*.csv"))
    files.extend(root.glob("*资金流*.csv"))

    rows, seen = [], set()
    skip_name_keys = [
        "frequency",
        "fundflow_missing",
        "repair_log",
        "fundflow_quality",
        "bad_source_files",
    ]
    for p in files:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        low = rp.lower()
        if not any(k in low for k in ["fundflow", "资金流", "pivot", "concept", "industry"]):
            continue
        name_low = p.name.lower()
        if any(k in name_low for k in skip_name_keys):
            continue
        try:
            st = p.stat()
            mtime = st.st_mtime
            size = st.st_size
        except Exception:
            mtime = 0
            size = 0
        rows.append({
            "path": p.as_posix(),
            "name": p.name,
            "kind_hint": detect_kind_from_name(p),
            "is_pivot": "pivot" in p.name.lower(),
            "size_bytes": size,
            "mtime": mtime,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime else "",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["is_pivot", "mtime"], ascending=[False, False]).reset_index(drop=True)
    return df


def parse_pivot_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    kind = detect_kind_from_name(p)
    if kind == "unknown":
        return pd.DataFrame()

    df = read_csv_safe(p)
    if df.empty:
        return pd.DataFrame()

    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed:
        df = df.drop(columns=unnamed)

    rc = find_rank_col(df)
    if rc is None:
        return pd.DataFrame()

    ds = get_date_cols(df, rc)
    if not ds:
        return pd.DataFrame()

    rows = []
    for _, r in df.iterrows():
        label = str(r.get(rc, "")).strip()
        if is_bad_cell(label):
            continue
        direction, rank_num = rank_info(label)
        if direction == "unknown":
            continue
        for dc in ds:
            cell = r.get(dc)
            if is_bad_cell(cell):
                continue
            amount = extract_amount(str(cell))
            name = extract_board_name(str(cell))
            if amount is None or not name:
                continue
            rows.append({
                "kind": kind,
                "date": normalize_date(dc),
                "direction": direction,
                "rank_num": rank_num,
                "board": name,
                "net_yi": amount,
                "rank_label": label,
                "cell": str(cell),
                "source_file": p.name,
                "source_path": p.as_posix(),
                "source_mtime": p.stat().st_mtime if p.exists() else 0,
            })
    return pd.DataFrame(rows)


def _parse_daily_like_csv_legacy(path: str | Path) -> pd.DataFrame:
    """
    兼容非 pivot 的日度表。只做保守识别：
    需要有名称列 + 净额列；日期从文件名或 date 列取。
    """
    p = Path(path)
    kind = detect_kind_from_name(p)
    if kind == "unknown":
        return pd.DataFrame()

    df = read_csv_safe(p)
    if df.empty:
        return pd.DataFrame()

    cols = list(df.columns)

    def find_col(cands):
        for c in cands:
            if c in cols:
                return c
        for cand in cands:
            for col in cols:
                if cand.lower() in str(col).lower():
                    return col
        return None

    name_col = find_col(["name", "板块名称", "名称", "概念名称", "行业名称", "f14"])
    net_col = find_col(["net_yi", "净流入", "主力净流入", "净额", "f62", "main_net_inflow_yuan"])
    date_col = find_col(["date", "日期", "trade_date"])
    rank_col = find_col(["rank", "排名", "序号"])

    if not name_col or not net_col:
        return pd.DataFrame()

    def to_num(x):
        if is_bad_cell(x):
            return None
        s = str(x).replace(",", "").replace("%", "").strip()
        mult = 1.0
        if s.endswith("亿"):
            s = s[:-1]
        elif s.endswith("万"):
            s = s[:-1]; mult = 1e-4
        elif s.endswith("元"):
            s = s[:-1]; mult = 1e-8
        try:
            return float(s) * mult
        except Exception:
            return None

    date_from_name = ""
    m = re.search(r"\d{4}-\d{2}-\d{2}|\d{8}", p.name)
    if m:
        date_from_name = normalize_date(m.group(0))

    out = pd.DataFrame()
    out["kind"] = kind
    out["date"] = df[date_col].map(normalize_date) if date_col else date_from_name
    out["board"] = df[name_col].astype(str)
    out["net_yi"] = df[net_col].map(to_num)
    out = out.dropna(subset=["net_yi"])
    if out.empty:
        return pd.DataFrame()

    # 按净额排序生成前/后排名
    rows = []
    for d, g in out.groupby("date"):
        g = g.copy()
        top = g.sort_values("net_yi", ascending=False).head(20)
        bot = g.sort_values("net_yi", ascending=True).head(20)
        for i, (_, r) in enumerate(top.iterrows(), 1):
            rows.append({**r.to_dict(), "direction": "inflow", "rank_num": i, "rank_label": f"前{i}", "cell": f"{r['board']}({r['net_yi']:.2f}亿)", "source_file": p.name, "source_path": p.as_posix(), "source_mtime": p.stat().st_mtime if p.exists() else 0})
        for i, (_, r) in enumerate(bot.iterrows(), 1):
            rows.append({**r.to_dict(), "direction": "outflow", "rank_num": i, "rank_label": f"后{i}", "cell": f"{r['board']}({r['net_yi']:.2f}亿)", "source_file": p.name, "source_path": p.as_posix(), "source_mtime": p.stat().st_mtime if p.exists() else 0})

    return pd.DataFrame(rows)


def parse_daily_like_csv(path: str | Path) -> pd.DataFrame:
    """
    Parse non-pivot daily outputs and reconstruct top/bottom rankings per date.
    Supports files that carry kind in filename or in a board_type column.
    """
    p = Path(path)
    default_kind = detect_kind_from_name(p)
    name_low = p.name.lower()
    if any(k in name_low for k in ["frequency", "fundflow_missing", "repair_log", "fundflow_quality"]):
        return pd.DataFrame()

    df = read_csv_safe(p)
    if df.empty:
        return pd.DataFrame()

    cols = list(df.columns)

    def find_col(cands: List[str]) -> Optional[str]:
        for c in cands:
            if c in cols:
                return c
        for cand in cands:
            c_low = cand.lower()
            for col in cols:
                if c_low in str(col).lower():
                    return col
        return None

    name_col = find_col(["board_name", "name", "f14", "板块名称", "名称", "概念名称", "行业名称"])
    net_col = find_col([
        "net_inflow_yi",
        "main_net_inflow_yi",
        "net_yi",
        "main_net_inflow_yuan",
        "f62",
        "净流入",
        "主力净流入",
        "净额",
    ])
    date_col = next((c for c in cols if str(c).strip() in {"date", "trade_date", "日期"}), None)
    board_type_col = find_col(["board_type", "kind", "type", "板块类型"])

    if not name_col or not net_col:
        return pd.DataFrame()

    def to_num(x):
        if is_bad_cell(x):
            return None
        try:
            return float(x)
        except Exception:
            pass
        s = str(x).replace(",", "").replace("%", "").strip()
        mult = 1.0
        if s.endswith("亿"):
            s = s[:-1]
        elif s.endswith("万"):
            s = s[:-1]
            mult = 1e-4
        elif s.endswith("元"):
            s = s[:-1]
            mult = 1e-8
        try:
            return float(s) * mult
        except Exception:
            return None

    def norm_kind(x: object) -> str:
        s = str(x).strip().lower()
        raw = str(x)
        if not s or s in {"nan", "none"}:
            return ""
        if "concept" in s or s == "gn" or "概念" in raw:
            return "concept"
        if "industry" in s or s == "hy" or "行业" in raw:
            return "industry"
        return ""

    date_from_name = ""
    m = re.search(r"\d{4}-\d{2}-\d{2}|\d{8}", p.name)
    if m:
        date_from_name = normalize_date(m.group(0))
    if not date_col and not date_from_name:
        return pd.DataFrame()

    out = pd.DataFrame()
    if board_type_col:
        k = df[board_type_col].map(norm_kind)
        if default_kind != "unknown":
            k = k.where(k != "", default_kind)
        out["kind"] = k
    else:
        if default_kind == "unknown":
            return pd.DataFrame()
        out["kind"] = default_kind

    out["date"] = df[date_col].astype(str).str.strip() if date_col else date_from_name
    out["date"] = out["date"].astype(str).str.strip()
    out = out[out["date"].str.match(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$", na=False)]
    out["date"] = out["date"].map(normalize_date)
    out["board"] = df[name_col].astype(str).str.strip()
    out["net_yi"] = df[net_col].map(to_num)
    out = out[(out["kind"].astype(str).str.len() > 0) & (out["board"].astype(str).str.len() > 0)].dropna(subset=["net_yi"])
    if out.empty:
        return pd.DataFrame()

    rows = []
    source_mtime = p.stat().st_mtime if p.exists() else 0
    for (k, d), g in out.groupby(["kind", "date"], dropna=False):
        g = g.copy()
        top = g.sort_values("net_yi", ascending=False).head(20)
        bot = g.sort_values("net_yi", ascending=True).head(20)
        for i, (_, r) in enumerate(top.iterrows(), 1):
            rows.append({
                "kind": k,
                "date": d,
                "direction": "inflow",
                "rank_num": i,
                "board": r["board"],
                "net_yi": r["net_yi"],
                "rank_label": f"前{i}",
                "cell": f"{r['board']}({float(r['net_yi']):+.2f}亿)",
                "source_file": p.name,
                "source_path": p.as_posix(),
                "source_mtime": source_mtime,
            })
        for i, (_, r) in enumerate(bot.iterrows(), 1):
            rows.append({
                "kind": k,
                "date": d,
                "direction": "outflow",
                "rank_num": i,
                "board": r["board"],
                "net_yi": r["net_yi"],
                "rank_label": f"后{i}",
                "cell": f"{r['board']}({float(r['net_yi']):+.2f}亿)",
                "source_file": p.name,
                "source_path": p.as_posix(),
                "source_mtime": source_mtime,
            })
    return pd.DataFrame(rows)


def rebuild_cache(project_root: str | Path) -> Dict[str, object]:
    root = Path(project_root).resolve()
    files = discover_source_csvs(root)

    frames = []
    bad_rows = []

    for _, r in files.iterrows():
        p = Path(r["path"])
        if not p.exists() or int(r["size_bytes"]) == 0:
            bad_rows.append({"path": p.as_posix(), "reason": "empty_or_missing"})
            continue

        try:
            parsed = parse_pivot_csv(p) if r["is_pivot"] else parse_daily_like_csv(p)
            if parsed.empty:
                # pivot 解析失败时不要当日度表再乱解析；否则容易污染
                bad_rows.append({"path": p.as_posix(), "reason": "parse_empty"})
            else:
                frames.append(parsed)
        except Exception as e:
            bad_rows.append({"path": p.as_posix(), "reason": f"{type(e).__name__}: {e}"})

    if frames:
        cache = pd.concat(frames, ignore_index=True)
        cache["date"] = cache["date"].map(normalize_date)
        cache["source_mtime"] = pd.to_numeric(cache["source_mtime"], errors="coerce").fillna(0)
        cache["rank_num"] = pd.to_numeric(cache["rank_num"], errors="coerce")

        cov = (
            cache.groupby(["source_path", "kind", "date", "direction"], dropna=False)["rank_num"]
            .nunique(dropna=True)
            .rename("source_rank_coverage")
            .reset_index()
        )
        cache = cache.merge(cov, on=["source_path", "kind", "date", "direction"], how="left")
        cache["source_rank_coverage"] = pd.to_numeric(cache["source_rank_coverage"], errors="coerce").fillna(0)

        # 同一 kind/date/direction/rank_num 保留最新来源
        cache = cache.sort_values(["source_rank_coverage", "source_mtime"]).drop_duplicates(
            subset=["kind", "date", "direction", "rank_num"],
            keep="last"
        ).reset_index(drop=True)
        cache = cache.drop(columns=["source_rank_coverage"], errors="ignore")
    else:
        cache = pd.DataFrame(columns=["kind", "date", "direction", "rank_num", "board", "net_yi", "rank_label", "cell", "source_file", "source_path", "source_mtime"])

    cp = cache_path(root)
    cache.to_csv(cp, index=False, encoding="utf-8-sig")

    bad = pd.DataFrame(bad_rows)
    bad_path = clean_root(root) / "cache" / "bad_source_files.csv"
    bad.to_csv(bad_path, index=False, encoding="utf-8-sig")

    return {
        "source_files": len(files),
        "parsed_frames": len(frames),
        "bad_files": len(bad_rows),
        "cache_rows": len(cache),
        "cache_path": cp.as_posix(),
        "bad_path": bad_path.as_posix(),
    }


def load_cache(project_root: str | Path) -> pd.DataFrame:
    cp = cache_path(project_root)
    if not cp.exists():
        return pd.DataFrame()
    df = read_csv_safe(cp)
    if df.empty:
        return pd.DataFrame()
    df["rank_num"] = pd.to_numeric(df["rank_num"], errors="coerce")
    df["net_yi"] = pd.to_numeric(df["net_yi"], errors="coerce")
    return df.dropna(subset=["date", "kind", "direction", "rank_num", "board", "net_yi"])


# =========================
# 缺失检测 / 透视 / 频次
# =========================

def missing_table(
    cache: pd.DataFrame,
    start: str,
    end: str,
    rank_n: int = 6,
    project_root: Optional[str | Path] = None,
) -> pd.DataFrame:
    """
    检查指定区间 concept / industry 的前N/后N是否完整。
    修复点：
    - 日期区间为空时也返回固定列，避免 KeyError: 'date'；
    - start > end 时自动交换；
    - cache 为空时也可输出缺失表。
    """
    columns = [
        "date", "kind", "front_valid", "back_valid",
        "front_missing", "back_missing", "complete"
    ]

    start_n = normalize_date(start)
    end_n = normalize_date(end)

    try:
        if pd.to_datetime(start_n) > pd.to_datetime(end_n):
            start_n, end_n = end_n, start_n
    except Exception:
        return pd.DataFrame(columns=columns)

    dates = trading_days(start_n, end_n, project_root=project_root)
    if not dates:
        return pd.DataFrame(columns=columns)

    rows = []
    for d in dates:
        for kind in ["concept", "industry"]:
            if cache is None or cache.empty:
                sub = pd.DataFrame()
            else:
                sub = cache[(cache["date"] == d) & (cache["kind"] == kind)]

            if sub.empty:
                front = 0
                back = 0
            else:
                front = sub[
                    (sub["direction"] == "inflow")
                    & (pd.to_numeric(sub["rank_num"], errors="coerce") <= rank_n)
                ]["rank_num"].nunique()

                back = sub[
                    (sub["direction"] == "outflow")
                    & (pd.to_numeric(sub["rank_num"], errors="coerce") <= rank_n)
                ]["rank_num"].nunique()

            rows.append({
                "date": d,
                "kind": kind,
                "front_valid": int(front),
                "back_valid": int(back),
                "front_missing": max(0, rank_n - int(front)),
                "back_missing": max(0, rank_n - int(back)),
                "complete": bool(front >= rank_n and back >= rank_n),
            })

    out = pd.DataFrame(rows, columns=columns)
    if out.empty:
        return pd.DataFrame(columns=columns)

    return out.sort_values(["date", "kind"]).reset_index(drop=True)


def build_pivot(cache: pd.DataFrame, start: str, end: str, kind: str, rank_n: int = 6) -> pd.DataFrame:
    if cache.empty:
        return pd.DataFrame()

    d = cache[(cache["kind"] == kind) & (cache["date"] >= normalize_date(start)) & (cache["date"] <= normalize_date(end)) & (cache["rank_num"] <= rank_n)].copy()
    if d.empty:
        return pd.DataFrame()

    rows = []
    for direction, prefix in [("inflow", "前"), ("outflow", "后")]:
        for i in range(1, rank_n + 1):
            row = {"排名": f"{prefix}{i}"}
            sub = d[(d["direction"] == direction) & (d["rank_num"] == i)]
            for _, r in sub.iterrows():
                row[r["date"]] = f"{r['board']}({float(r['net_yi']):+.2f}亿)"
            rows.append(row)
    return pd.DataFrame(rows)


def frequency_stats(cache: pd.DataFrame, start: str, end: str, kind: str, top_n: int = 3) -> pd.DataFrame:
    if cache.empty:
        return pd.DataFrame()

    d = cache[(cache["kind"] == kind) & (cache["date"] >= normalize_date(start)) & (cache["date"] <= normalize_date(end)) & (cache["rank_num"] <= top_n)].copy()
    if d.empty:
        return pd.DataFrame()

    inflow = d[d["direction"] == "inflow"].groupby("board").agg(
        进前三次数=("date", "count"),
        进前三天数=("date", lambda x: len(set(x))),
        进前三累计净流入_亿=("net_yi", "sum"),
        最近进前三日期=("date", "max"),
        平均进前三排名=("rank_num", "mean"),
    )

    outflow = d[d["direction"] == "outflow"].groupby("board").agg(
        末三次数=("date", "count"),
        末三天数=("date", lambda x: len(set(x))),
        末三累计净流出_亿=("net_yi", "sum"),
        最近末三日期=("date", "max"),
        平均末三排名=("rank_num", "mean"),
    )

    s = inflow.join(outflow, how="outer").fillna(0).reset_index()
    s["净强势天数"] = s["进前三天数"] - s["末三天数"]
    s["累计净额_亿"] = s["进前三累计净流入_亿"] + s["末三累计净流出_亿"]
    s["强度分"] = s["进前三天数"] * 10 + s["净强势天数"] * 5 + s["累计净额_亿"] / 50 - s["末三天数"] * 8
    return s.sort_values(["强度分", "进前三天数", "累计净额_亿"], ascending=False).reset_index(drop=True)


def trend_data(cache: pd.DataFrame, start: str, end: str, kind: str, boards: List[str]) -> pd.DataFrame:
    if cache.empty or not boards:
        return pd.DataFrame()
    d = cache[(cache["kind"] == kind) & (cache["date"] >= normalize_date(start)) & (cache["date"] <= normalize_date(end)) & (cache["board"].isin(boards))].copy()
    if d.empty:
        return pd.DataFrame()
    # 同一板块同一天可能同时在流入/流出极端里，只保留绝对排名最靠前的记录
    d["sort_rank"] = d["rank_num"]
    d = d.sort_values(["date", "board", "sort_rank"]).drop_duplicates(["date", "board"], keep="first")
    return d[["date", "kind", "board", "net_yi", "direction", "rank_num"]]


def export_current(project_root: str | Path, cache: pd.DataFrame, start: str, end: str, rank_n: int = 6, top_n: int = 3) -> Dict[str, str]:
    root = clean_root(project_root) / "exports"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    sub = cache[(cache["date"] >= normalize_date(start)) & (cache["date"] <= normalize_date(end))].copy()
    long_path = root / f"fundflow_long_{normalize_date(start)}_{normalize_date(end)}_{ts}.csv"
    sub.to_csv(long_path, index=False, encoding="utf-8-sig")

    miss = missing_table(cache, start, end, rank_n, project_root=project_root)
    miss_path = root / f"fundflow_missing_{normalize_date(start)}_{normalize_date(end)}_{ts}.csv"
    miss.to_csv(miss_path, index=False, encoding="utf-8-sig")

    paths = {"long": long_path.as_posix(), "missing": miss_path.as_posix()}

    for kind in ["concept", "industry"]:
        piv = build_pivot(cache, start, end, kind, rank_n)
        p = root / f"fundflow_pivot_{kind}_{normalize_date(start)}_{normalize_date(end)}_{ts}.csv"
        piv.to_csv(p, index=False, encoding="utf-8-sig")
        paths[f"pivot_{kind}"] = p.as_posix()

        freq = frequency_stats(cache, start, end, kind, top_n)
        fp = root / f"fundflow_top{top_n}_frequency_{kind}_{normalize_date(start)}_{normalize_date(end)}_{ts}.csv"
        freq.to_csv(fp, index=False, encoding="utf-8-sig")
        paths[f"frequency_{kind}"] = fp.as_posix()

    return paths


# =========================
# 东方财富 Cookie / 补采执行
# =========================

def cookie_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / "eastmoney_cookie.txt"


def read_cookie(project_root: str | Path) -> str:
    p = cookie_path(project_root)
    return p.read_text(encoding="utf-8", errors="ignore").strip() if p.exists() else ""


def save_cookie(project_root: str | Path, text: str) -> Path:
    p = cookie_path(project_root)
    p.write_text(text.strip(), encoding="utf-8")
    return p


def cookie_status(project_root: str | Path) -> Dict[str, object]:
    text = read_cookie(project_root)
    c = SimpleCookie()
    try:
        c.load(text)
    except Exception:
        pass
    parsed = {k: v.value for k, v in c.items()}
    keys = set(parsed)
    important = ["qgqp_b_id", "st_si", "st_pvi", "st_sp", "HAList", "em_hq_fls"]
    return {
        "exists": bool(text),
        "length": len(text),
        "parsed_count": len(parsed),
        "has_keys": [k for k in important if k in keys],
        "missing_common_keys": [k for k in important if k not in keys],
        "path": cookie_path(project_root).as_posix(),
    }


def open_eastmoney_verify_pages() -> List[str]:
    urls = [
        "https://data.eastmoney.com/bkzj/gn.html",
        "https://data.eastmoney.com/bkzj/hy.html",
        "https://quote.eastmoney.com/center/boardlist.html#concept_board",
    ]
    for u in urls:
        try:
            webbrowser.open(u)
        except Exception:
            pass
    return urls


def test_cookie_request(project_root: str | Path) -> Dict[str, object]:
    try:
        import requests
    except Exception as e:
        return {"ok": False, "error": f"requests未安装：{e}"}

    text = read_cookie(project_root)
    if not text:
        return {"ok": False, "error": "eastmoney_cookie.txt为空"}

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "5",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:90+t:3",
        "fields": "f12,f14,f3,f62,f184",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/bkzj/gn.html", "Cookie": text}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        try:
            js = r.json()
        except Exception:
            return {"ok": False, "status_code": r.status_code, "error": "返回非JSON，可能被验证页拦截", "preview": r.text[:300]}
        diff = (js.get("data") or {}).get("diff") or []
        return {"ok": bool(diff), "status_code": r.status_code, "rows": len(diff), "preview": diff[:2]}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _normalize_stock_code(stock: str) -> str:
    s = re.sub(r"\D", "", str(stock or "").strip())
    return s[-6:] if len(s) >= 6 else s


def _normalize_market(stock: str, market: str = "") -> str:
    m = str(market or "").strip().lower()
    if m in {"sh", "sz", "bj"}:
        return m
    if stock.startswith(("4", "8")):
        return "bj"
    if stock.startswith(("0", "2", "3")):
        return "sz"
    return "sh"


def fetch_stock_fund_flow_em(
    project_root: str | Path,
    stock: str,
    market: str = "",
    start: str = "",
    end: str = "",
    retries: int = 2,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """
    东方财富个股资金流最小抓取（优先 no_beg_end，必要时再尝试带 beg/end）。
    返回: (明细DataFrame, 元信息dict)
    """
    try:
        import requests
    except Exception as e:
        return pd.DataFrame(), {"ok": False, "error": f"requests未安装：{e}", "stock": stock}

    code = _normalize_stock_code(stock)
    if len(code) != 6:
        return pd.DataFrame(), {"ok": False, "error": f"股票代码无效：{stock}", "stock": stock}

    market_norm = _normalize_market(code, market)
    market_no = {"sh": "1", "sz": "0", "bj": "0"}[market_norm]
    cookie_text = read_cookie(project_root)
    retries = max(1, int(retries))

    base_params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{market_no}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    variants: List[Tuple[str, Dict[str, str]]] = [("no_beg_end", dict(base_params))]

    start_key = compact_date(start) if str(start).strip() else ""
    end_key = compact_date(end) if str(end).strip() else ""
    if start_key or end_key:
        p = dict(base_params)
        if start_key:
            p["beg"] = start_key
        if end_key:
            p["end"] = end_key
        variants.append(("with_beg_end", p))

    urls = ["https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"]

    attempts: List[Dict[str, object]] = []
    last_error = ""

    for mode, params_base in variants:
        for url in urls:
            for attempt in range(1, retries + 1):
                params = dict(params_base)
                params["_"] = str(int(time.time() * 1000))
                try:
                    session = requests.Session()
                    session.trust_env = False
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"
                        ),
                        "Accept": "application/json,text/plain,*/*",
                        "Referer": "https://data.eastmoney.com/zjlx/detail.html",
                        "Connection": "keep-alive",
                    }
                    if cookie_text:
                        headers["Cookie"] = cookie_text

                    r = session.get(url, params=params, headers=headers, timeout=20)
                    status = int(r.status_code)
                    try:
                        js = r.json()
                    except Exception as e:
                        err = f"json_error: {repr(e)}"
                        attempts.append(
                            {
                                "mode": mode,
                                "url": url,
                                "attempt": attempt,
                                "ok": False,
                                "status_code": status,
                                "error": err,
                            }
                        )
                        last_error = err
                        continue

                    klines = ((js.get("data") or {}).get("klines") or []) if isinstance(js, dict) else []
                    if status != 200 or not klines:
                        msg = ""
                        if isinstance(js, dict):
                            msg = str(js.get("message") or js.get("msg") or "")
                        err = f"status={status}; rows={len(klines)}; msg={msg}"
                        attempts.append(
                            {
                                "mode": mode,
                                "url": url,
                                "attempt": attempt,
                                "ok": False,
                                "status_code": status,
                                "error": err,
                            }
                        )
                        last_error = err
                        continue

                    rows = [str(x).split(",") for x in klines]
                    df = pd.DataFrame(rows)
                    cols = [
                        "日期",
                        "主力净流入",
                        "小单净流入",
                        "中单净流入",
                        "大单净流入",
                        "超大单净流入",
                        "主力净流入净占比",
                        "小单净流入净占比",
                        "中单净流入净占比",
                        "大单净流入净占比",
                        "超大单净流入净占比",
                        "收盘价",
                        "涨跌幅",
                        "扩展1",
                        "扩展2",
                    ]
                    if df.shape[1] < 13:
                        err = f"字段数异常：{df.shape[1]}"
                        attempts.append(
                            {
                                "mode": mode,
                                "url": url,
                                "attempt": attempt,
                                "ok": False,
                                "status_code": status,
                                "error": err,
                            }
                        )
                        last_error = err
                        continue

                    df = df.iloc[:, : min(df.shape[1], len(cols))].copy()
                    df.columns = cols[: df.shape[1]]
                    if "日期" in df.columns:
                        df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.strftime("%Y-%m-%d")

                    for c in df.columns:
                        if c != "日期":
                            df[c] = pd.to_numeric(df[c], errors="coerce")

                    for c in ["主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"]:
                        if c in df.columns:
                            df[f"{c}(亿元)"] = df[c] / 1e8

                    if "日期" in df.columns:
                        df = df.sort_values("日期").reset_index(drop=True)

                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    save_path = clean_root(project_root) / "logs" / f"stock_fundflow_em_{code}_{mode}_{ts}.csv"
                    df.to_csv(save_path, index=False, encoding="utf-8-sig")

                    min_date = str(df["日期"].min()) if ("日期" in df.columns and not df.empty) else ""
                    max_date = str(df["日期"].max()) if ("日期" in df.columns and not df.empty) else ""
                    attempts.append(
                        {
                            "mode": mode,
                            "url": url,
                            "attempt": attempt,
                            "ok": True,
                            "status_code": status,
                            "error": "",
                        }
                    )

                    return df, {
                        "ok": True,
                        "stock": code,
                        "market": market_norm,
                        "mode": mode,
                        "url": url,
                        "rows": int(len(df)),
                        "min_date": min_date,
                        "max_date": max_date,
                        "saved": save_path.as_posix(),
                        "attempts": attempts,
                    }
                except Exception as e:
                    err = repr(e)
                    attempts.append(
                        {
                            "mode": mode,
                            "url": url,
                            "attempt": attempt,
                            "ok": False,
                            "status_code": 0,
                            "error": err,
                        }
                    )
                    last_error = err
                    time.sleep(min(1.2, 0.25 * attempt))

    # 回退：实时抓取失败时，尝试读取最近一次本地成功文件，避免页面空白。
    logs_dir = clean_root(project_root) / "logs"
    fallback_files = sorted(logs_dir.glob(f"stock_fundflow_em_{code}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not fallback_files:
        fallback_files = sorted(
            logs_dir.glob(f"min_test_stock_{code}_em_detail_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if fallback_files:
        p = fallback_files[0]
        df = read_csv_safe(p)
        if not df.empty:
            rename_map = {
                "date": "日期",
                "main_net_inflow": "主力净流入",
                "small_net_inflow": "小单净流入",
                "medium_net_inflow": "中单净流入",
                "big_net_inflow": "大单净流入",
                "super_big_net_inflow": "超大单净流入",
                "main_net_ratio": "主力净流入净占比",
                "small_net_ratio": "小单净流入净占比",
                "medium_net_ratio": "中单净流入净占比",
                "big_net_ratio": "大单净流入净占比",
                "super_big_net_ratio": "超大单净流入净占比",
                "close": "收盘价",
                "change_pct": "涨跌幅",
            }
            df = df.rename(columns=rename_map)
            if "日期" in df.columns:
                df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            for c in df.columns:
                if c != "日期":
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            for c in ["主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"]:
                if c in df.columns and f"{c}(亿元)" not in df.columns:
                    df[f"{c}(亿元)"] = pd.to_numeric(df[c], errors="coerce") / 1e8
            if "日期" in df.columns:
                df = df.sort_values("日期").reset_index(drop=True)

            return df, {
                "ok": True,
                "stock": code,
                "market": market_norm,
                "mode": "cache_fallback",
                "url": "",
                "rows": int(len(df)),
                "min_date": str(df["日期"].min()) if "日期" in df.columns and not df.empty else "",
                "max_date": str(df["日期"].max()) if "日期" in df.columns and not df.empty else "",
                "saved": p.as_posix(),
                "error": last_error or "",
                "attempts": attempts,
            }

    return pd.DataFrame(), {
        "ok": False,
        "stock": code,
        "market": market_norm,
        "rows": 0,
        "error": last_error or "抓取失败",
        "attempts": attempts,
    }


def python_exe(project_root: str | Path) -> str:
    p = Path(project_root).resolve() / ".venv" / "Scripts" / "python.exe"
    return p.as_posix() if p.exists() else sys.executable


def build_repair_commands(project_root: str | Path, dates: List[str], kind: str = "both", rank_n: int = 6, command_template: str = "") -> List[List[str]]:
    root = Path(project_root).resolve()

    if command_template.strip():
        out = []
        for d in dates:
            dashed = normalize_date(d)
            compact = compact_date(d)
            cmd = command_template.format(
                date=compact,
                date_dash=dashed,
                start=compact,
                end=compact,
                start_dash=dashed,
                end_dash=dashed,
                kind=kind,
                topn=rank_n,
                rank_n=rank_n,
            )
            out.append(["cmd", "/c", cmd])
        return out

    # 默认沿用现有东方财富采集脚本。参数不匹配时，页面日志会提示，再用自定义命令模板。
    candidates = [
        root / "fundflow_cookie_clean.py",
        root / "08_eastmoney_market_fundflow_cookie.py",
    ]
    script = next((p for p in candidates if p.exists()), None)
    if script is None:
        return []

    out = []
    for d in dates:
        dashed = normalize_date(d)
        out.append([python_exe(root), script.as_posix(), "--start", dashed, "--end", dashed, "--type", kind, "--topn", str(rank_n)])
    return out


def _cmd_meta(cmd: List[str]) -> Dict[str, object]:
    """Parse date/type/topn hints from one repair command for resume routing."""
    tokens = [str(x) for x in cmd]
    low = [t.lower() for t in tokens]

    def value_of(*keys: str) -> str:
        for i, t in enumerate(low):
            for k in keys:
                kk = k.lower()
                if t == kk and i + 1 < len(tokens):
                    return tokens[i + 1]
                if t.startswith(kk + "="):
                    return tokens[i].split("=", 1)[1]
        return ""

    start = value_of("--start", "--start_dash")
    end = value_of("--end", "--end_dash")
    kind = (value_of("--type", "--kind") or "both").lower()
    topn_s = value_of("--topn", "--rank_n")

    dt = ""
    if start and end and normalize_date(start) == normalize_date(end):
        dt = normalize_date(start)
    else:
        joined = " ".join(tokens)
        m = re.search(r"\d{4}-\d{2}-\d{2}|\d{8}", joined)
        if m:
            dt = normalize_date(m.group(0))

    topn = None
    try:
        if topn_s:
            topn = int(float(topn_s))
    except Exception:
        topn = None

    return {
        "date": dt,
        "kind": kind if kind in {"concept", "industry", "both"} else "both",
        "topn": topn,
        "cmd": " ".join(tokens),
    }


def _remaining_dates(
    cache: pd.DataFrame,
    verify_dates: List[str],
    verify_kind: str,
    verify_rank_n: int,
    project_root: Optional[str | Path] = None,
) -> List[str]:
    if not verify_dates:
        return []
    ds = sorted({normalize_date(x) for x in verify_dates if str(x).strip()})
    if not ds:
        return []
    miss = missing_table(cache, ds[0], ds[-1], rank_n=int(verify_rank_n), project_root=project_root)
    if miss.empty:
        return []
    miss = miss[miss["date"].isin(ds)]
    if verify_kind in {"concept", "industry"}:
        miss = miss[miss["kind"] == verify_kind]
    bad = miss[~miss["complete"]]
    if bad.empty:
        return []
    return sorted(bad["date"].dropna().astype(str).unique().tolist())


def _write_resume_state(project_root: str | Path, payload: Dict[str, object]) -> None:
    p = resume_state_path(project_root)
    payload = dict(payload)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def run_commands(
    project_root: str | Path,
    commands: List[List[str]],
    timeout: int = 240,
    max_rounds: int = 1,
    verify_dates: Optional[List[str]] = None,
    verify_kind: str = "both",
    verify_rank_n: int = 6,
    auto_resume: bool = True,
    pause_seconds: float = 1.0,
) -> pd.DataFrame:
    root = Path(project_root).resolve()
    rows: List[Dict[str, object]] = []
    metas = [_cmd_meta(c) for c in commands]

    verify_dates = [normalize_date(x) for x in (verify_dates or []) if str(x).strip()]
    if not verify_dates:
        verify_dates = sorted({str(m.get("date", "")).strip() for m in metas if str(m.get("date", "")).strip()})

    # Round 0 pre-check: if cache already complete, skip complete dates up-front.
    pending = list(range(len(commands)))
    pre_remaining = verify_dates[:]
    if auto_resume and verify_dates:
        cache0 = load_cache(root)
        if not cache0.empty:
            pre_remaining = _remaining_dates(
                cache0,
                verify_dates,
                verify_kind,
                int(verify_rank_n),
                project_root=root,
            )
            if pre_remaining:
                pending = [i for i in pending if (not metas[i]["date"]) or (str(metas[i]["date"]) in pre_remaining)]
            else:
                pending = []

    max_rounds = max(1, int(max_rounds))
    timeout = int(timeout)
    pause_seconds = max(0.0, float(pause_seconds))

    if not pending:
        msg = "all_selected_dates_complete_before_run" if verify_dates else "no_pending_commands"
        _write_resume_state(root, {
            "status": msg,
            "verify_dates": verify_dates,
            "remaining_dates": [],
            "verify_kind": verify_kind,
            "verify_rank_n": int(verify_rank_n),
            "max_rounds": max_rounds,
        })
        return pd.DataFrame([{
            "round": 0,
            "cmd_idx": "",
            "cmd": "",
            "date": "",
            "kind": verify_kind,
            "returncode": "SKIP",
            "seconds": 0.0,
            "remaining_dates": "",
            "remaining_count": 0,
            "stdout_tail": msg,
            "stderr_tail": "",
        }])

    final_remaining = verify_dates[:]
    for round_i in range(1, max_rounds + 1):
        if not pending:
            break

        for idx in pending:
            cmd = commands[idx]
            meta = metas[idx]
            t0 = time.time()
            try:
                p = subprocess.run(
                    cmd,
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="ignore",
                )
                rows.append({
                    "round": round_i,
                    "cmd_idx": idx,
                    "cmd": " ".join(cmd),
                    "date": meta.get("date", ""),
                    "kind": meta.get("kind", verify_kind),
                    "returncode": p.returncode,
                    "seconds": round(time.time() - t0, 2),
                    "stdout_tail": (p.stdout or "")[-1500:],
                    "stderr_tail": (p.stderr or "")[-1500:],
                })
            except Exception as e:
                rows.append({
                    "round": round_i,
                    "cmd_idx": idx,
                    "cmd": " ".join(cmd),
                    "date": meta.get("date", ""),
                    "kind": meta.get("kind", verify_kind),
                    "returncode": "ERROR",
                    "seconds": round(time.time() - t0, 2),
                    "stdout_tail": "",
                    "stderr_tail": repr(e),
                })

        # Resume verification: rebuild + recheck remaining dates.
        if auto_resume and verify_dates:
            rebuild_info = rebuild_cache(root)
            cache = load_cache(root)
            final_remaining = _remaining_dates(
                cache,
                verify_dates,
                verify_kind,
                int(verify_rank_n),
                project_root=root,
            )

            if final_remaining:
                next_pending: List[int] = []
                for idx in pending:
                    d = str(metas[idx].get("date", "")).strip()
                    if (not d) or (d in final_remaining):
                        next_pending.append(idx)
                pending = next_pending
            else:
                pending = []

            _write_resume_state(root, {
                "status": "running" if pending else "completed",
                "verify_dates": verify_dates,
                "remaining_dates": final_remaining,
                "verify_kind": verify_kind,
                "verify_rank_n": int(verify_rank_n),
                "max_rounds": max_rounds,
                "last_round": round_i,
                "rebuild_info": rebuild_info,
            })

            # Backfill round summary onto this round's rows.
            for r in rows:
                if int(r.get("round", 0) or 0) == round_i:
                    r["remaining_dates"] = ",".join(final_remaining)
                    r["remaining_count"] = len(final_remaining)

            if not pending:
                break
            if round_i < max_rounds and pause_seconds > 0:
                time.sleep(pause_seconds)
        else:
            final_remaining = []
            for r in rows:
                if int(r.get("round", 0) or 0) == round_i:
                    r["remaining_dates"] = ""
                    r["remaining_count"] = 0

    if auto_resume and verify_dates:
        _write_resume_state(root, {
            "status": "completed" if not final_remaining else "max_rounds_reached",
            "verify_dates": verify_dates,
            "remaining_dates": final_remaining,
            "verify_kind": verify_kind,
            "verify_rank_n": int(verify_rank_n),
            "max_rounds": max_rounds,
        })

    return pd.DataFrame(rows)
