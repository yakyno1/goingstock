#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xueqiu_format_check.py

用途：
一次性检查 goingstock 雪球采集系统所有关键文件格式：
1）xueqiu_cookie.txt
2）names_input.txt
3）watchlist_xueqiu.csv
4）outputs_xueqiu/xueqiu_capture_*.csv
5）outputs_xueqiu/xueqiu_theme_*.csv
6）outputs_xueqiu/xueqiu_packet_*.md
7）outputs_xueqiu/history/*_kline*.csv

运行：
    cd D:\ai_projects\goingstock
    python xueqiu_format_check.py

输出：
    outputs_xueqiu/format_check_时间.md
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Dict, List, Tuple

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "outputs_xueqiu"
HIST_DIR = OUT_DIR / "history"


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ok(flag: bool) -> str:
    return "OK" if flag else "ERROR"


def warn(flag: bool) -> str:
    return "OK" if flag else "WARN"


def read_csv_safe(path: Path) -> Tuple[pd.DataFrame | None, str]:
    try:
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig"), ""
    except Exception as e:
        try:
            return pd.read_csv(path, dtype=str), ""
        except Exception as e2:
            return None, f"{type(e2).__name__}: {e2}"


def is_xueqiu_symbol(s: str) -> bool:
    s = str(s).strip().upper()
    return bool(
        re.match(r"^(SH|SZ)\d{6}$", s)
        or re.match(r"^\d{5}$", s)    # 雪球港股：00981 / 01810
        or re.match(r"^US[A-Z0-9.]+$", s)
    )


def check_cookie(lines: List[str]) -> None:
    path = SCRIPT_DIR / "xueqiu_cookie.txt"
    exists = path.exists()
    length = 0
    has_token = False
    has_placeholder = False

    if exists:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        real_lines = [x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith("#")]
        text = " ".join(real_lines)
        length = len(text)
        has_token = "xq_a_token=" in text
        has_placeholder = "这里粘贴" in text or "你的完整雪球Cookie" in text

    lines.append("## 1. xueqiu_cookie.txt")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 文件存在 | {ok(exists)} | {path} |")
    lines.append(f"| Cookie长度>100 | {ok(length > 100)} | 当前长度 {length} |")
    lines.append(f"| 包含xq_a_token | {ok(has_token)} | 必须包含登录token |")
    lines.append(f"| 无模板占位符 | {ok(not has_placeholder)} | 不应包含“这里粘贴”等字样 |")
    lines.append("")


def check_names_input(lines: List[str]) -> None:
    path = SCRIPT_DIR / "names_input.txt"
    lines.append("## 2. names_input.txt")
    lines.append("")
    if not path.exists():
        lines.append(f"ERROR：不存在 {path}")
        lines.append("")
        return

    bad = []
    rows = []
    for i, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="ignore").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) > 3:
            bad.append((i, line, "列数超过3，格式应为：股票名,主题,市场或代码"))
        if not parts[0]:
            bad.append((i, line, "股票名为空"))
        rows.append((i, parts))

    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 有效行数 | {ok(len(rows) > 0)} | {len(rows)} 行 |")
    lines.append(f"| 坏行数量 | {ok(len(bad) == 0)} | {len(bad)} 行 |")
    if bad:
        lines.append("")
        lines.append("| 行号 | 内容 | 问题 |")
        lines.append("|---:|---|---|")
        for i, line, reason in bad[:20]:
            lines.append(f"| {i} | `{line}` | {reason} |")
    lines.append("")


def check_watchlist(lines: List[str]) -> None:
    path = SCRIPT_DIR / "watchlist_xueqiu.csv"
    lines.append("## 3. watchlist_xueqiu.csv")
    lines.append("")
    if not path.exists():
        lines.append(f"ERROR：不存在 {path}")
        lines.append("")
        return

    df, err = read_csv_safe(path)
    if df is None:
        lines.append(f"ERROR：读取失败 {err}")
        lines.append("")
        return

    required = ["symbol", "name", "theme"]
    missing = [c for c in required if c not in df.columns]
    bad_symbols = []
    duplicated = []

    if not missing:
        for idx, row in df.iterrows():
            sym = str(row["symbol"]).strip()
            if not is_xueqiu_symbol(sym):
                bad_symbols.append((idx + 2, sym, row.get("name", "")))
        duplicated = df[df["symbol"].duplicated(keep=False)]["symbol"].tolist()

    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 必要列symbol/name/theme | {ok(not missing)} | 缺失：{missing} |")
    lines.append(f"| 行数>0 | {ok(len(df) > 0)} | {len(df)} 行 |")
    lines.append(f"| symbol格式 | {ok(len(bad_symbols) == 0)} | 坏代码 {len(bad_symbols)} 个；A股SH/SZ+6位，港股5位纯数字 |")
    lines.append(f"| 无重复symbol | {warn(len(duplicated) == 0)} | 重复：{sorted(set(duplicated))[:20]} |")

    if bad_symbols:
        lines.append("")
        lines.append("| 行号 | symbol | name | 问题 |")
        lines.append("|---:|---|---|---|")
        for rowno, sym, name in bad_symbols[:50]:
            lines.append(f"| {rowno} | `{sym}` | {name} | symbol格式不符合雪球规则 |")
    lines.append("")


def latest_file(pattern: str, folder: Path = OUT_DIR) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def check_capture(lines: List[str]) -> None:
    lines.append("## 4. 最新 xueqiu_capture_*.csv")
    lines.append("")
    path = latest_file("xueqiu_capture_*.csv")
    if not path:
        lines.append("WARN：未找到 capture 文件")
        lines.append("")
        return

    df, err = read_csv_safe(path)
    if df is None:
        lines.append(f"ERROR：读取失败 {path} {err}")
        lines.append("")
        return

    required = [
        "capture_time", "symbol", "name", "theme", "current", "percent",
        "open_gap_pct", "from_open_pct", "from_high_pct",
        "dist_ma5_pct", "dist_ma20_pct", "amount_vs_ma20",
        "pos_20d_pct", "strength_tags", "risk_tags", "action_hint"
    ]
    missing = [c for c in required if c not in df.columns]

    lines.append(f"文件：`{path}`")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 必要列完整 | {ok(not missing)} | 缺失：{missing} |")
    lines.append(f"| 行数>0 | {ok(len(df) > 0)} | {len(df)} 行 |")
    if "error" in df.columns:
        errors = df[df["error"].fillna("").astype(str).str.len() > 0]
        lines.append(f"| 行情失败行 | {warn(len(errors) == 0)} | {len(errors)} 行 |")
    lines.append("")


def check_theme(lines: List[str]) -> None:
    lines.append("## 5. 最新 xueqiu_theme_*.csv")
    lines.append("")
    path = latest_file("xueqiu_theme_*.csv")
    if not path:
        lines.append("WARN：未找到 theme 文件")
        lines.append("")
        return

    df, err = read_csv_safe(path)
    if df is None:
        lines.append(f"ERROR：读取失败 {path} {err}")
        lines.append("")
        return

    required = ["theme", "count", "avg_percent", "max_percent", "strong_count", "amount_sum", "avg_amount_vs_ma20"]
    missing = [c for c in required if c not in df.columns]

    lines.append(f"文件：`{path}`")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 必要列完整 | {ok(not missing)} | 缺失：{missing} |")
    lines.append(f"| 行数>0 | {ok(len(df) > 0)} | {len(df)} 行 |")
    lines.append("")


def check_packet(lines: List[str]) -> None:
    lines.append("## 6. 最新 xueqiu_packet_*.md")
    lines.append("")
    path = latest_file("xueqiu_packet_*.md")
    if not path:
        lines.append("WARN：未找到 packet 文件")
        lines.append("")
        return

    text = path.read_text(encoding="utf-8", errors="ignore")
    needed = ["## 1.", "## 2.", "请 ChatGPT 判断"]
    missing = [x for x in needed if x not in text]

    lines.append(f"文件：`{path}`")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| Markdown结构 | {ok(not missing)} | 缺失：{missing} |")
    lines.append(f"| 文件长度>100 | {ok(len(text) > 100)} | {len(text)} 字符 |")
    lines.append("")


def check_history(lines: List[str]) -> None:
    lines.append("## 7. history K线文件")
    lines.append("")
    if not HIST_DIR.exists():
        lines.append("WARN：未找到 history 目录")
        lines.append("")
        return

    files = sorted(HIST_DIR.glob("*_kline*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        lines.append("WARN：未找到 history K线文件")
        lines.append("")
        return

    required_core = ["date", "open", "high", "low", "close", "volume", "amount"]
    result_rows = []

    for path in files[:30]:
        df, err = read_csv_safe(path)
        if df is None:
            result_rows.append((path.name, "ERROR", f"读取失败：{err}"))
            continue

        first_col_ok = len(df.columns) > 0 and df.columns[0] == "date"
        missing = [c for c in required_core if c not in df.columns]
        has_ma = any(c.startswith("ma") for c in df.columns)
        has_ret = any(c.startswith("ret_") for c in df.columns)
        raw_junk = [c for c in ["pe", "pb", "ps", "pcf", "market_capital", "balance"] if c in df.columns]

        status = "OK" if first_col_ok and not missing and has_ma and has_ret and not raw_junk else "WARN"
        detail = []
        if not first_col_ok:
            detail.append("date不是第一列")
        if missing:
            detail.append(f"缺核心列{missing}")
        if not has_ma:
            detail.append("无MA列")
        if not has_ret:
            detail.append("无收益率列")
        if raw_junk:
            detail.append(f"含原始杂项列{raw_junk}")
        if not detail:
            detail.append(f"{len(df)}行，{len(df.columns)}列")
        result_rows.append((path.name, status, "；".join(detail)))

    lines.append("| 文件 | 状态 | 说明 |")
    lines.append("|---|---|---|")
    for name, status, detail in result_rows:
        lines.append(f"| `{name}` | {status} | {detail} |")
    lines.append("")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    lines: List[str] = []
    lines.append(f"# 雪球系统格式检查报告 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    check_cookie(lines)
    check_names_input(lines)
    check_watchlist(lines)
    check_capture(lines)
    check_theme(lines)
    check_packet(lines)
    check_history(lines)

    report = "\n".join(lines)
    out = OUT_DIR / f"format_check_{now_tag()}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print("\n报告已保存：", out)


if __name__ == "__main__":
    main()
