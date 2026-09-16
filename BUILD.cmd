@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

py -3 -m pip install -r requirements.txt
if errorlevel 1 goto :error

py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name Cevirgec ^
  --icon "assets\app.ico" ^
  --add-data "translation_prompt.txt;." ^
  --add-data "assets;assets" ^
  gui.py
if errorlevel 1 goto :error

copy /Y config.json dist\config.json >nul
copy /Y translation_prompt.txt dist\translation_prompt.txt >nul
echo.
echo Hazir: %CD%\dist\Cevirgec.exe
exit /b 0

:error
echo.
echo DERLEME BASARISIZ.
pause
exit /b 1
