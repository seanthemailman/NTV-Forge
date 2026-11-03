@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Download Portable Tools.ps1"
if errorlevel 1 (echo Tool download failed.& pause& exit /b 1)
echo Tools downloaded. You can now run Build Windows EXE.bat.
pause
