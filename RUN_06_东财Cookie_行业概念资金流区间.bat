@echo off
chcp 65001 >nul
cd /d %~dp0

set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
set NO_PROXY=*
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install pandas requests tabulate

python 06_eastmoney_board_fundflow_cookie.py --start 20260519 --end 20260521 --type both --topn 6 --max-boards 120 --rank-pz 1000
pause
