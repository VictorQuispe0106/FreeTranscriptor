@echo off
rem Launches the Transcriptor en vivo desktop app.
rem First run bootstraps the project .venv and installs requirements.txt.
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"

rem Pick a Python launcher: py (python.org install) or python on PATH.
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo Python no esta instalado o no esta en el PATH.
  echo Instala Python 3.12 o mas nuevo desde https://www.python.org/downloads/
  echo   marcando "Add python.exe to PATH" al instalar.
  pause
  exit /b 1
)

rem The pinned dependency wheels need Python 3.12+.
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" || (
  echo Se necesita Python 3.12 o mas nuevo. Instalalo desde
  echo https://www.python.org/downloads/ marcando "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo Primera vez: creando el entorno .venv e instalando dependencias...
  echo Esto tarda varios minutos y necesita internet una sola vez.
  %PYEXE% -m venv "%VENV%" || goto :fail
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  echo Entorno listo.
)

start "" "%VENV%\Scripts\pythonw.exe" "%~dp0transcriptor_app.py"
exit /b 0

:fail
echo No se pudo preparar el entorno .venv. Revisa el mensaje de arriba,
echo borra la carpeta .venv y vuelve a correr este archivo.
pause
exit /b 1
