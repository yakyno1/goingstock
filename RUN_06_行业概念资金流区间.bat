@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate.bat
python 05_sector_concept_fundflow_range.py --start 20260422 --end 20260522 --topn 6 --type both --max-boards 80
pause
