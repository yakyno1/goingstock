@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat
python 02_ak_news_events_plus.py --mode intraday --hours 2 --max 120 --keywords "长鑫存储,AI算力,半导体,CPO,光模块,英伟达,H200,财报,业绩,公告,控制权,并购,小米,美图,工业富联,德赛西威"
pause
