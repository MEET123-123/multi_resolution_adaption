@echo off
setlocal
cd /d "%~dp0\..\.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
%PY% studio_reverse\browser_automation\server.py --port 8797
pause
