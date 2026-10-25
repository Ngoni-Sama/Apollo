@echo off
title Apollo
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Apollo isn't installed yet. Double-click install.bat first.
  pause
  exit /b 1
)
echo Starting Apollo. Your browser will open in a moment.
echo Keep this window open while you use Apollo. Close it to stop Apollo.
echo.
".venv\Scripts\python.exe" src\ui.py --open
if errorlevel 1 pause
