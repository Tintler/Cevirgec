@echo off
chcp 65001 >nul
cd /d "%~dp0"
pushd web
if not exist "node_modules" (
  echo Arayuz bagimliliklari kuruluyor ^(ilk calistirma, Node.js gerekir^)...
  call npm ci
  if errorlevel 1 (popd & goto :error)
)
rem Arayuz her acilista derlenir (~1 sn); git pull sonrasi degisiklikler boylece gelir.
call npm run build --silent >nul
if errorlevel 1 (popd & goto :error)
popd
py -3 desktop.py
if errorlevel 1 pause
exit /b 0

:error
echo.
echo ARAYUZ DERLENEMEDI. Node.js kurulu oldugundan emin olun: https://nodejs.org
echo Bagimliliklar degistiyse web klasorunde "npm ci" calistirin.
pause
exit /b 1
