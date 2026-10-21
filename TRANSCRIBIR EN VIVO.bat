@echo off
setlocal
cd /d "%~dp0"
title Transcribir en vivo - Live Captions

powershell -NoProfile -Command "if (-not (Get-Process -Name LiveCaptions -ErrorAction SilentlyContinue)) { exit 1 }"
if errorlevel 1 goto nocaptions

echo.
echo   Leyendo Subtitulos en vivo.
echo   El transcript se guarda en la carpeta transcripts.
echo   Ctrl+C para cortar.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0captions_watch.ps1"
echo.
echo   Listo. Transcript guardado.
pause
exit /b 0

:nocaptions
echo.
echo   Windows Live Captions no esta abierto.
echo.
echo   1. Apreta Win + Ctrl + L
echo   2. Espera a que aparezca la barra de Subtitulos en vivo
echo   3. Volve a ejecutar este archivo
echo.
pause
exit /b 1
