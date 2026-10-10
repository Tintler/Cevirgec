@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "web\dist\index.html" (
  echo Arayuz derleniyor ^(ilk calistirma, Node.js gerekir^)...
  pushd web
  call npm ci
  if errorlevel 1 (popd & goto :error)
  call npm run build
  if errorlevel 1 (popd & goto :error)
  popd
)
py -3 desktop.py
if errorlevel 1 pause
exit /b 0

:error
echo.
echo ARAYUZ DERLENEMEDI. Node.js kurulu oldugundan emin olun: https://nodejs.org
pause
exit /b 1
