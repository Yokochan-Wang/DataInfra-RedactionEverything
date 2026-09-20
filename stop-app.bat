@echo off
chcp 65001 >nul
setlocal
rem ==== DataInfra-RedactionEverything: stop local stack ====
set "NODE_HOME=C:\Users\zkjg\AppData\Local\Programs\node-v24.19.0-win-x64"
if exist "%NODE_HOME%\node.exe" set "PATH=%NODE_HOME%;%PATH%"
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] node.exe not found. Install Node 24 or fix NODE_HOME in this file.
  pause
  exit /b 1
)
node scripts\local\stop-stack.mjs
echo.
pause
