@echo off
cd /d %~dp0
echo Starting installation...
venv\Scripts\pip.exe install -r requirements.txt
echo Installation finished.
echo Starting debug...
venv\Scripts\python.exe debug.py
echo Starting bot...
venv\Scripts\python.exe main.py
pause

