@echo off
title Apollo installer
echo Installing Apollo. This window will ask you a question at the end.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\install.ps1" %*
echo.
pause
