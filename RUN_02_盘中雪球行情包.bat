@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat
python 01_xueqiu_intraday_capture.py --watchlist watchlist_xueqiu.csv --cookie-file xueqiu_cookie.txt --kline-count 120
pause
