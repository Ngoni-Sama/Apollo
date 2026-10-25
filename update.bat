@echo off
title Update Apollo
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\update.ps1"
echo.
pause
