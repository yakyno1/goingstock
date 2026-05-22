# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime
import pandas as pd
import streamlit as st
from ui_utils import *
page_header("07｜系统健康检查", "Cookie、watchlist、输出格式、接口日志集中检查。")

st.subheader("基础文件检查")
checks=[]
for path, desc in [(ROOT/'xueqiu_cookie.txt','雪球Cookie'),(ROOT/'watchlist_xueqiu.csv','雪球自选表'),(ROOT/'names_input.txt','股票名输入'),(ROOT/'requirements.txt','依赖文件')]:
    checks.append({'项目':desc,'路径':str(path),'状态':'OK' if path.exists() else '缺失','大小KB':round(path.stat().st_size/1024,1) if path.exists() else 0})
st.dataframe(pd.DataFrame(checks), use_container_width=True)

if st.button("运行雪球格式检查", type="primary"):
    run_and_show("04_xueqiu_format_check.py", [], timeout=600)

st.divider()
st.subheader("最新格式检查报告")
fc=latest_file("format_check_*.md", ROOT/"outputs_xueqiu")
if fc: show_file_card(fc, "fmt")

st.subheader("接口日志总览")
logs=list_files(["outputs_news/logs/*.csv", "outputs_fundflow/logs/*.csv", "outputs_market_fundflow/logs/*.csv", "outputs_overnight/logs/*.csv"], ROOT, 20)
if logs:
    for p in logs[:6]:
        with st.expander(str(p.relative_to(ROOT))):
            st.dataframe(read_csv_any(p), use_container_width=True)
else:
    st.info("暂无日志。")
