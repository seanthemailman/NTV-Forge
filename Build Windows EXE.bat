@echo off
setlocal
cd /d "%~dp0"
echo Building NTV Forge 1.0 for Windows (silent tray release)...
echo.

rem Check tools BEFORE PyInstaller clears the previous distribution.
if not exist "tools\ffmpeg.exe" goto missingtools
if not exist "tools\yt-dlp.exe" goto missingtools
if not exist "tools\deno.exe" goto missingtools
if not exist "tools\streamlink-portable\bin\streamlink.exe" goto missingtools
if not exist "project.json" if not exist "dist\NTV Forge\project.json" goto missingproject

rem Preserve user channels/settings from the existing distribution.
rem Keep this backup if the build fails, so the configuration can be recovered.
if exist "dist\NTV Forge\project.json" (
  copy /Y "dist\NTV Forge\project.json" "project.build-backup.json" >nul
  if errorlevel 1 goto fail
  echo Backed up existing project.json to project.build-backup.json
)

where python >nul 2>&1
if not errorlevel 1 (
  set "PYTHON=python"
  goto build
)
where py >nul 2>&1
if errorlevel 1 goto fail
set "PYTHON=py -3"

:build
%PYTHON% -m pip install --upgrade pyinstaller pystray pillow
if errorlevel 1 goto fail
%PYTHON% -m PyInstaller --noconfirm --clean --onedir --windowed --name "NTV Forge" --add-data "index.html;." --add-data "suite.html;." --add-data "channel_forge;channel_forge" --hidden-import channel_forge.app tray_launcher.py
if errorlevel 1 goto fail

if exist "project.build-backup.json" (
  copy /Y "project.build-backup.json" "dist\NTV Forge\project.json" >nul
) else (
  copy /Y "project.json" "dist\NTV Forge\project.json" >nul
)
if errorlevel 1 goto fail

if not exist "dist\NTV Forge\tools" mkdir "dist\NTV Forge\tools"
if errorlevel 1 goto fail
copy /Y "tools\ffmpeg.exe" "dist\NTV Forge\tools\ffmpeg.exe" >nul
if errorlevel 1 goto fail
if exist "tools\ffprobe.exe" (
  copy /Y "tools\ffprobe.exe" "dist\NTV Forge\tools\ffprobe.exe" >nul
  if errorlevel 1 goto fail
)
copy /Y "tools\yt-dlp.exe" "dist\NTV Forge\tools\yt-dlp.exe" >nul
if errorlevel 1 goto fail
copy /Y "tools\deno.exe" "dist\NTV Forge\tools\deno.exe" >nul
if errorlevel 1 goto fail
xcopy /E /I /Y "tools\streamlink-portable" "dist\NTV Forge\tools\streamlink-portable" >nul
if errorlevel 1 goto fail
if exist "tools\THIRD_PARTY" (
  xcopy /E /I /Y "tools\THIRD_PARTY" "dist\NTV Forge\tools\THIRD_PARTY" >nul
  if errorlevel 1 goto fail
)

if not exist "dist\NTV Forge\NTV Forge.exe" goto fail
if not exist "dist\NTV Forge\project.json" goto fail
if not exist "dist\NTV Forge\tools\ffmpeg.exe" goto fail
if not exist "dist\NTV Forge\tools\yt-dlp.exe" goto fail
if not exist "dist\NTV Forge\tools\deno.exe" goto fail
if not exist "dist\NTV Forge\tools\streamlink-portable\bin\streamlink.exe" goto fail

if exist "project.build-backup.json" del /Q "project.build-backup.json"
echo.
echo [OK] NTV Forge.exe
echo [OK] project.json
echo [OK] ffmpeg.exe
echo [OK] yt-dlp.exe
echo [OK] deno.exe
echo [OK] streamlink.exe
 echo.
echo BUILD COMPLETE. All required portable tools are present.
echo Test dist\NTV Forge\NTV Forge.exe before distributing.
echo Package the entire dist\NTV Forge folder. Never share just the EXE.
pause
exit /b 0

:missingtools
echo ERROR: One or more portable tools are missing from the source tools folder.
echo Required: ffmpeg.exe, yt-dlp.exe, deno.exe, streamlink-portable\bin\streamlink.exe
echo Run Download Portable Tools.bat FIRST, then rebuild.
goto fail

:missingproject
echo ERROR: project.json not found in the source folder or existing distribution.
goto fail

:fail
echo.
echo BUILD FAILED. See error messages above.
echo If project.build-backup.json exists, keep it until your project is restored.
echo Close NTV Forge in the system tray before retrying.
pause
exit /b 1
