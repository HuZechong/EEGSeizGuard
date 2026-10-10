@echo off
setlocal
cd /d "%~dp0"
if exist "E:\anaconda1\envs\pytorch\python.exe" (
  "E:\anaconda1\envs\pytorch\python.exe" -m pip install -r requirements.txt
) else (
  python -m pip install -r requirements.txt
)
pause
endlocal
