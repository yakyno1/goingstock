@echo off
chcp 65001 >nul
cd /d %~dp0
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
set NO_PROXY=*
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python 08_xueqiu_live_news_collect.py --mode all --hours 24 --max 200
pause
