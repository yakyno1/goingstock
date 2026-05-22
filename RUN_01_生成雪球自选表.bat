@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat
python 03_xueqiu_name_to_watchlist.py
pause
