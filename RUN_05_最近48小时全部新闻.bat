@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat
python 02_ak_news_events_plus.py --mode all --hours 48 --max 300
pause
