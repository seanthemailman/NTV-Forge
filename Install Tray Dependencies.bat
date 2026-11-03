@echo off
setlocal
cd /d "%~dp0"
echo Installing NTV Forge tray requirements...
where python >nul 2>&1
if not errorlevel 1 (
  python -m pip install --upgrade pystray pillow
  if not errorlevel 1 goto success
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -m pip install --upgrade pystray pillow
  if not errorlevel 1 goto success
)
echo.
echo ERROR: Could not install pystray and Pillow.
echo Check your Python installation and internet connection.
pause
exit /b 1
:success
echo.
echo Dependencies installed. You can launch NTV Forge now.
pause
exit /b 0
