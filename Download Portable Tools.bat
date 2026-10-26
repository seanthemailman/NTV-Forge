@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Download Portable Tools.ps1"
if errorlevel 1 (echo Tool download failed. See message above.) else (echo Tools downloaded.)
pause
