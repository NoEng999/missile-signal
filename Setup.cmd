@echo off
cd /d "%~dp0"
if not exist "runtime\Scripts\python.exe" python -m venv runtime
"runtime\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
"runtime\Scripts\python.exe" install_signal.py
if errorlevel 1 goto error
echo Setup completed. Open Start.cmd.
pause
exit /b 0
:error
echo Setup failed. See the message above.
pause
exit /b 1
