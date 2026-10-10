@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Ses motoru paketlerini derler: dist-engines\cevirgec-ses-<id>-win64.zip
py -3 -m pip install -r requirements.txt -r requirements-tts.txt
if errorlevel 1 goto :error
py -3 build_engines.py %*
if errorlevel 1 goto :error
echo.
echo Hazir: %CD%\dist-engines
exit /b 0

:error
echo.
echo SES MOTORU DERLEMESI BASARISIZ.
pause
exit /b 1
