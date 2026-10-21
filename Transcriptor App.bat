@echo off
rem Launches the Transcriptor en vivo desktop app (no shell needed while GUI runs).
setlocal
set PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe
if exist "%PYW%" (
  start "" "%PYW%" "%~dp0transcriptor_app.py"
) else (
  start "" py "%~dp0transcriptor_app.py"
)
exit /b 0
