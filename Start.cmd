@echo off
cd /d "%~dp0"
if not exist "runtime\Scripts\pythonw.exe" (
  echo Run Setup.cmd first.
  pause
  exit /b 1
)
start "" "runtime\Scripts\pythonw.exe" "app.py"
