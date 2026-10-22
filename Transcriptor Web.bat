@echo off
title Transcriptor Web GUI
rem Launches the local web GUI server; it opens the browser itself.
rem Keep the console open: closing it stops the server (Ctrl+C also works).
cd /d "%~dp0"
set PYC=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
if exist "%PYC%" (
  "%PYC%" web_gui.py
) else (
  py web_gui.py
)
