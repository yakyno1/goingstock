# -*- coding: utf-8 -*-
from __future__ import annotations
import shutil
import pandas as pd
import streamlit as st
from ui_utils import *
page_header("03｜雪球盘中行情包", "雪球为盘中主源：批量行情、主题摘要、日K历史、Markdown 输入包。")

st.subheader("A. 可选：从股票名生成 watchlist_xueqiu.csv")
default_names = read_text_any(ROOT/"names_input.txt") or "工业富联,AI服务器,A\n中际旭创,CPO/光模块,A\n新易盛,CPO/光模块,A\n美图公司,AI应用,HK\n小米集团-W,消费电子/汽车,HK"
text = st.text_area("names_input.txt 内容", default_names, height=180)
col1,col2=st.columns(2)
with col1:
    if st.button("保存 names_input.txt"):
        (ROOT/"names_input.txt").write_text(text, encoding="utf-8-sig")
        st.success("已保存")
with col2:
    if st.button("生成候选 watchlist"):
        run_and_show("03_xueqiu_name_to_watchlist.py", [], timeout=1200)

watch_candidates=list_files(["watchlist_from_names_*.csv"], ROOT/"outputs_xueqiu", 5)
if watch_candidates:
    sel=st.selectbox("选择候选 watchlist 覆盖正式 watchlist_xueqiu.csv", watch_candidates, format_func=lambda p:p.name)
    st.dataframe(read_csv_any(sel), use_container_width=True)
    if st.button("确认覆盖 watchlist_xueqiu.csv"):
        shutil.copyfile(sel, ROOT/"watchlist_xueqiu.csv")
        st.success("已覆盖 watchlist_xueqiu.csv")

st.divider()
st.subheader("B. 运行雪球盘中行情采集")
kline_count=st.number_input("日K历史数量", 60, 500, 120, step=20)
if st.button("运行雪球盘中行情包", type="primary"):
    run_and_show("01_xueqiu_intraday_capture.py", ["--kline-count", str(kline_count)], timeout=1800)

st.divider()
cap=latest_file("xueqiu_capture_*.csv", ROOT/"outputs_xueqiu")
theme=latest_file("xueqiu_theme_*.csv", ROOT/"outputs_xueqiu")
md=latest_file("xueqiu_packet_*.md", ROOT/"outputs_xueqiu")
raw=latest_file("xueqiu_raw_*.json", ROOT/"outputs_xueqiu")
if cap:
    df=read_csv_any(cap)
    st.subheader("最新个股盘中总表")
    st.dataframe(df, use_container_width=True, height=420)
    if not df.empty:
        tabs=st.tabs(["涨跌幅", "日内位置", "成交额/量能", "风险标签"])
        with tabs[0]:
            if "percent" in df.columns:
                show=df.sort_values("percent", ascending=False)
                plot_bar_zero(show, "name", "percent", color="theme" if "theme" in show.columns else None, title="个股涨跌幅（%）")
        with tabs[1]:
            cols=[c for c in ["from_open_pct","from_low_pct","from_high_pct","open_gap_pct"] if c in df.columns]
            if cols and px is not None:
                m=df[["name"]+cols].melt(id_vars="name", var_name="指标", value_name="数值")
                fig=px.bar(m, x="name", y="数值", color="指标", barmode="group", title="日内位置/开盘缺口（%）")
                fig.add_hline(y=0, line_dash="dash")
                st.plotly_chart(fig, use_container_width=True)
        with tabs[2]:
            if "amount" in df.columns:
                tmp=df.copy(); tmp["amount_yi"]=pd.to_numeric(tmp["amount"],errors="coerce")/100000000
                plot_bar_zero(tmp.sort_values("amount_yi", ascending=False), "name", "amount_yi", color="theme" if "theme" in tmp.columns else None, title="成交额（亿元）")
            if "amount_vs_ma20" in df.columns:
                plot_bar_zero(df.sort_values("amount_vs_ma20", ascending=False), "name", "amount_vs_ma20", color="theme" if "theme" in df.columns else None, title="成交额 / 20日均额")
        with tabs[3]:
            keep=[c for c in ["symbol","name","theme","strength_tags","risk_tags","action_hint","error"] if c in df.columns]
            st.dataframe(df[keep], use_container_width=True)
if theme:
    st.subheader("主题强弱摘要")
    tdf=read_csv_any(theme); st.dataframe(tdf, use_container_width=True)
    if "avg_percent" in tdf.columns: plot_bar_zero(tdf, "theme", "avg_percent", title="主题平均涨跌幅（%）")
if md:
    st.subheader("Markdown 输入包")
    show_file_card(md, "xq_md")
