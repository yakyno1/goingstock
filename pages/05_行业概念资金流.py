# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import pandas as pd
import streamlit as st
import altair as alt

from fundflow_rebuild_clean_core import (
    project_root_from_page,
    rebuild_cache,
    load_cache,
    missing_table,
    build_pivot,
    frequency_stats,
    trend_data,
    export_current,
    cookie_status,
    save_cookie,
    read_cookie,
    open_eastmoney_verify_pages,
    test_cookie_request,
    build_repair_commands,
    run_commands,
    clean_root,
    fetch_stock_fund_flow_em,
    latest_output_files,
    cleanup_generated_outputs,
)

st.set_page_config(page_title="行业概念资金流", layout="wide")

PROJECT_ROOT = project_root_from_page(__file__)

st.title("行业概念资金流｜重做版")
st.caption("东方财富来源不变；增量检查缺失日期，不再每次从头跑；核心输出横向透视表、进前三频次、趋势与补采。")

today = date.today()
default_end = today
default_start = today - timedelta(days=183)

with st.sidebar:
    st.header("参数")
    start = st.date_input("开始日期", value=default_start)
    end = st.date_input("结束日期", value=default_end)
    pivot_rank_n = st.number_input("横向透视表前/后N", min_value=3, max_value=20, value=6, step=1)
    freq_top_n = st.number_input("频次统计前/后N", min_value=1, max_value=10, value=3, step=1)

start_s = start.strftime("%Y-%m-%d")
end_s = end.strftime("%Y-%m-%d")

tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs(["数据索引/缺失补采", "横向透视表", "进前三频次", "板块趋势", "东方财富验证", "导出"])

with tab0:
    st.subheader("1. 重建索引")
    st.write("重建索引只扫描现有输出文件，把可用数据合并成统一长表缓存；不会联网。")

    if st.button("扫描现有输出并重建缓存", type="primary", use_container_width=True):
        with st.spinner("正在扫描 outputs_fundflow / outputs_market_fundflow / outputs_combined ..."):
            info = rebuild_cache(PROJECT_ROOT)
        st.success("缓存已重建")
        st.json(info)

    cache = load_cache(PROJECT_ROOT)
    if cache.empty:
        st.warning("当前缓存为空。请先点击“扫描现有输出并重建缓存”。")
    else:
        st.success(f"当前缓存行数：{len(cache)}；日期范围：{cache['date'].min()} ~ {cache['date'].max()}")
        st.dataframe(cache.tail(50), use_container_width=True, height=260)

    st.subheader("2. 缺失检测")
    miss = missing_table(cache, start_s, end_s, int(pivot_rank_n), project_root=PROJECT_ROOT) if not cache.empty else pd.DataFrame()
    if miss.empty:
        st.info("没有可检测数据。")
    else:
        bad = miss[~miss["complete"]].copy()
        if bad.empty:
            st.success("该时间段内未发现前/后N缺失。")
        else:
            st.error(f"发现缺失记录：{len(bad)} 条")
            st.dataframe(bad, use_container_width=True, height=320)

            dates = sorted(bad["date"].drop_duplicates().tolist())
            selected_dates = st.multiselect("选择要补采的日期", dates, default=dates)

            c1, c2, c3 = st.columns(3)
            with c1:
                kind = st.selectbox("补采类型", ["both", "concept", "industry"], index=0)
            with c2:
                timeout = st.number_input("单次命令超时秒", min_value=30, max_value=1200, value=240, step=30)
            with c3:
                run_rank_n = st.number_input("补采前/后N", min_value=3, max_value=20, value=int(pivot_rank_n), step=1)
            c4, c5 = st.columns(2)
            with c4:
                max_rounds = st.number_input("自动重试轮次", min_value=1, max_value=10, value=3, step=1)
            with c5:
                pause_seconds = st.number_input("轮次间隔秒", min_value=0.0, max_value=10.0, value=0.8, step=0.2)
            auto_resume = st.checkbox("自动断点续采（每轮后自动重建缓存并复检）", value=True)

            with st.expander("自定义补采命令模板"):
                st.caption("默认会尝试调用 fundflow_cookie_clean.py；如果参数不匹配，在这里填你的真实命令。可用变量：{date_dash}, {date}, {kind}, {topn}")
                template = st.text_input(
                    "命令模板，可空",
                    value="",
                    placeholder="python fundflow_cookie_clean.py --start {date_dash} --end {date_dash} --type {kind} --topn {topn}",
                )

            commands = build_repair_commands(PROJECT_ROOT, selected_dates, kind=kind, rank_n=int(run_rank_n), command_template=template)
            with st.expander("即将执行命令", expanded=False):
                if commands:
                    for c in commands:
                        st.code(" ".join(c))
                else:
                    st.warning("没有可执行命令。检查 fundflow_cookie_clean.py 是否存在，或填写自定义命令模板。")

            if st.button("只补采选中缺失日期", type="primary", use_container_width=True):
                if not commands:
                    st.error("没有可执行命令。")
                else:
                    with st.spinner("正在补采。不要关闭页面。"):
                        log = run_commands(
                            PROJECT_ROOT,
                            commands,
                            timeout=int(timeout),
                            max_rounds=int(max_rounds),
                            verify_dates=selected_dates,
                            verify_kind=kind,
                            verify_rank_n=int(run_rank_n),
                            auto_resume=bool(auto_resume),
                            pause_seconds=float(pause_seconds),
                        )
                    st.dataframe(log, use_container_width=True, height=360)

                    log_dir = clean_root(PROJECT_ROOT) / "logs"
                    log_path = log_dir / f"repair_log_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    log.to_csv(log_path, index=False, encoding="utf-8-sig")
                    st.success(f"补采日志已保存：{log_path}")
                    state_path = clean_root(PROJECT_ROOT) / "cache" / "repair_resume_state.json"
                    if state_path.exists():
                        st.caption(f"断点续采状态：{state_path}")

                    # 补采后立即复检
                    cache_after = load_cache(PROJECT_ROOT)
                    miss_after = missing_table(cache_after, start_s, end_s, int(run_rank_n), project_root=PROJECT_ROOT) if not cache_after.empty else pd.DataFrame()
                    if not miss_after.empty:
                        miss_after = miss_after[miss_after["date"].isin(selected_dates)]
                        if kind in {"concept", "industry"}:
                            miss_after = miss_after[miss_after["kind"] == kind]
                    bad_after = miss_after[~miss_after["complete"]].copy() if not miss_after.empty else pd.DataFrame()
                    if bad_after.empty:
                        st.success("复检通过：选中日期在当前补采后已满足前/后N完整度。")
                    else:
                        st.warning(f"复检仍有缺失：{len(bad_after)} 条。可继续补采（会自动从未完成日期续跑）。")
                        st.dataframe(bad_after, use_container_width=True, height=260)

    st.subheader("3. 输出文件管理（避免文件太多太乱）")
    latest_df = latest_output_files(PROJECT_ROOT)
    if latest_df.empty:
        st.info("暂无可展示的最新输出文件。")
    else:
        st.caption("下表展示各目录最近文件，优先查看目录 `latest` 下的固定文件名。")
        st.dataframe(latest_df, use_container_width=True, height=260)

    c1, c2 = st.columns([1, 1.2])
    with c1:
        keep_n = st.number_input("每目录保留最近时间戳文件数", min_value=5, max_value=200, value=30, step=5)
    with c2:
        if st.button("整理历史输出文件（仅删旧时间戳文件）", use_container_width=True):
            info = cleanup_generated_outputs(PROJECT_ROOT, keep_latest=int(keep_n))
            st.success(f"整理完成：删除 {info.get('deleted_files', 0)} 个旧文件")
            st.json(info)
            latest_df2 = latest_output_files(PROJECT_ROOT)
            if not latest_df2.empty:
                st.dataframe(latest_df2, use_container_width=True, height=240)

with tab1:
    cache = load_cache(PROJECT_ROOT)
    st.subheader("横向透视表")
    if cache.empty:
        st.warning("缓存为空。")
    else:
        c1, c2 = st.columns(2)
        with c1:
            concept_pivot = build_pivot(cache, start_s, end_s, "concept", int(pivot_rank_n))
            st.markdown("### 概念资金流横向透视表")
            st.dataframe(concept_pivot, use_container_width=True, height=360)
        with c2:
            industry_pivot = build_pivot(cache, start_s, end_s, "industry", int(pivot_rank_n))
            st.markdown("### 行业资金流横向透视表")
            st.dataframe(industry_pivot, use_container_width=True, height=360)

with tab2:
    cache = load_cache(PROJECT_ROOT)
    st.subheader(f"进前三/末三频次统计：{start_s} ~ {end_s}")
    if cache.empty:
        st.warning("缓存为空。")
    else:
        for label, kind in [("概念", "concept"), ("行业", "industry")]:
            st.markdown(f"### {label}")
            fs = frequency_stats(cache, start_s, end_s, kind, int(freq_top_n))
            if fs.empty:
                st.info("缺数据")
                continue

            c1, c2 = st.columns(2)
            with c1:
                d = fs.sort_values("进前三天数", ascending=False).head(20)
                ch = alt.Chart(d).mark_bar().encode(
                    x=alt.X("进前三天数:Q"),
                    y=alt.Y("board:N", sort="-x", title="板块"),
                    tooltip=["board", "进前三次数", "进前三天数", "末三天数", alt.Tooltip("累计净额_亿:Q", format=".2f"), alt.Tooltip("强度分:Q", format=".2f")]
                ).properties(height=420, title=f"{label} 进前三天数 Top20")
                st.altair_chart(ch, use_container_width=True)
            with c2:
                d = fs.sort_values("强度分", ascending=False).head(20)
                ch = alt.Chart(d).mark_bar().encode(
                    x=alt.X("强度分:Q"),
                    y=alt.Y("board:N", sort="-x", title="板块"),
                    tooltip=["board", "进前三天数", "末三天数", "净强势天数", alt.Tooltip("累计净额_亿:Q", format=".2f"), alt.Tooltip("强度分:Q", format=".2f")]
                ).properties(height=420, title=f"{label} 综合强度 Top20")
                st.altair_chart(ch, use_container_width=True)

            st.dataframe(fs, use_container_width=True, height=360)

with tab3:
    cache = load_cache(PROJECT_ROOT)
    st.subheader("板块资金趋势")
    if cache.empty:
        st.warning("缓存为空。")
    else:
        kind = st.selectbox("选择类型", ["concept", "industry"], format_func=lambda x: "概念" if x == "concept" else "行业")
        fs = frequency_stats(cache, start_s, end_s, kind, int(freq_top_n))
        default_boards = fs.head(8)["board"].tolist() if not fs.empty else []
        boards = st.multiselect("选择板块", sorted(cache[cache["kind"] == kind]["board"].drop_duplicates().tolist()), default=default_boards)
        td = trend_data(cache, start_s, end_s, kind, boards)
        if td.empty:
            st.info("无趋势数据")
        else:
            ch = alt.Chart(td).mark_line(point=True).encode(
                x=alt.X("date:O", sort=sorted(td["date"].unique().tolist())),
                y=alt.Y("net_yi:Q", title="净流入/亿元"),
                color="board:N",
                tooltip=["date", "board", "direction", "rank_num", alt.Tooltip("net_yi:Q", format=".2f")]
            ).properties(height=460)
            st.altair_chart(ch, use_container_width=True)
            st.dataframe(td, use_container_width=True, height=300)

with tab4:
    st.subheader("东方财富验证 / Cookie")
    st.warning("这里不绕过验证。做法是浏览器正常完成东财验证，然后保存 Cookie。")

    st.json(cookie_status(PROJECT_ROOT))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("打开东方财富验证页面", use_container_width=True):
            st.write(open_eastmoney_verify_pages())
    with c2:
        if st.button("测试 Cookie 是否可用", use_container_width=True):
            st.json(test_cookie_request(PROJECT_ROOT))

    cookie_text = st.text_area("粘贴 Cookie", value=read_cookie(PROJECT_ROOT), height=180)
    if st.button("保存 Cookie", type="primary", use_container_width=True):
        p = save_cookie(PROJECT_ROOT, cookie_text)
        st.success(f"已保存：{p}")
        st.json(cookie_status(PROJECT_ROOT))

    st.markdown("""
步骤：
1. 打开东方财富验证页面；
2. 手动完成滑块/验证；
3. F12 → Network → 刷新；
4. 找 `push2.eastmoney.com` 或 `data.eastmoney.com` 请求；
5. 复制 Request Headers 里的 Cookie；
6. 保存后点击测试，确认 `ok=true` 且 `rows>0`。
""")

    st.divider()
    st.subheader("东财个股资金流最小测试（自填股票）")
    c1, c2, c3, c4 = st.columns([1.1, 1.0, 0.9, 1.0])
    with c1:
        stock_input = st.text_input("股票代码", value="601138", help="支持 6 位代码，如 601138")
    with c2:
        market_label = st.selectbox("市场", ["自动", "沪市(sh)", "深市(sz)", "北交所(bj)"], index=0)
    with c3:
        retry_n = st.number_input("重试次数", min_value=1, max_value=5, value=2, step=1)
    with c4:
        back_days = st.number_input("默认回看天数", min_value=5, max_value=240, value=30, step=5)

    s1, s2, s3 = st.columns([1, 1, 1.2])
    with s1:
        stock_start = st.date_input("个股开始日期", value=today - timedelta(days=int(back_days)))
    with s2:
        stock_end = st.date_input("个股结束日期", value=today)
    with s3:
        use_range = st.checkbox("向东财请求时附带 beg/end（偶发不稳定）", value=False)

    stock_start_s = stock_start.strftime("%Y-%m-%d")
    stock_end_s = stock_end.strftime("%Y-%m-%d")
    market_map = {"自动": "", "沪市(sh)": "sh", "深市(sz)": "sz", "北交所(bj)": "bj"}

    if st.button("测试东财个股资金流", type="primary", use_container_width=True):
        req_start = stock_start_s if use_range else ""
        req_end = stock_end_s if use_range else ""
        with st.spinner("正在抓取东财个股资金流..."):
            df_stock, meta = fetch_stock_fund_flow_em(
                PROJECT_ROOT,
                stock=stock_input,
                market=market_map.get(market_label, ""),
                start=req_start,
                end=req_end,
                retries=int(retry_n),
            )

        meta_view = {k: v for k, v in meta.items() if k != "attempts"}
        if meta.get("ok"):
            df_show = df_stock.copy()
            if "日期" in df_show.columns:
                ds = pd.to_datetime(df_show["日期"], errors="coerce")
                mask = (ds >= pd.to_datetime(stock_start_s)) & (ds <= pd.to_datetime(stock_end_s))
                df_show = df_show[mask].copy()

            if not df_show.empty and "日期" in df_show.columns:
                st.success(
                    f"抓取成功：{meta.get('stock', '')}，筛选后 {len(df_show)} 行，"
                    f"{df_show['日期'].min()} ~ {df_show['日期'].max()}"
                )
            else:
                st.warning(f"抓取成功但当前时间范围无数据：{stock_start_s} ~ {stock_end_s}")
            st.caption(f"来源文件：{meta.get('saved', '')}")
            st.markdown("#### 个股资金流明细（中文表头）")
            show_cols = [
                "日期",
                "收盘价",
                "涨跌幅",
                "主力净流入(亿元)",
                "主力净流入净占比",
                "超大单净流入(亿元)",
                "大单净流入(亿元)",
                "中单净流入(亿元)",
                "小单净流入(亿元)",
            ]
            keep_cols = [c for c in show_cols if c in df_show.columns]
            st.dataframe(df_show[keep_cols] if keep_cols else df_show, use_container_width=True, height=360)
            meta_view["筛选开始"] = stock_start_s
            meta_view["筛选结束"] = stock_end_s
            meta_view["筛选后行数"] = int(len(df_show))
        else:
            st.error(f"抓取失败：{meta.get('error', '未知错误')}")

        st.json(meta_view)
        attempts = pd.DataFrame(meta.get("attempts", []))
        if not attempts.empty:
            st.markdown("#### 请求尝试明细")
            st.dataframe(attempts, use_container_width=True, height=220)

with tab5:
    cache = load_cache(PROJECT_ROOT)
    st.subheader("导出给 B01 / GPT")
    if cache.empty:
        st.warning("缓存为空。")
    else:
        if st.button("导出当前时间段全部结果", type="primary", use_container_width=True):
            paths = export_current(PROJECT_ROOT, cache, start_s, end_s, int(pivot_rank_n), int(freq_top_n))
            st.success("已导出")
            st.json(paths)
