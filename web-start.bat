@echo off
REM ============================================================
REM  qi-agent Web Shell one-click start (serve + web, no browser)
REM  Usage: double-click, or: web-start.bat [serve_port] [web_port]
REM ============================================================
setlocal

set SERVE_PORT=%1
if "%SERVE_PORT%"=="" set SERVE_PORT=8765
set WEB_PORT=%2
if "%WEB_PORT%"=="" set WEB_PORT=9000

cd /d "%~dp0"

echo ============================================
echo  qi-agent Web Shell start
echo  serve port: %SERVE_PORT%  ^|  web port: %WEB_PORT%
echo ============================================

REM Check frontend build output (dist missing = need build first)
if not exist "qi_agent\web\frontend\dist\index.html" (
    echo [WARN] Frontend not built! Run:
    echo   cd qi_agent\web\frontend ^&^& npm install ^&^& npm run build
)

REM Start kernel serve (own window, stays visible)
echo [1/2] Starting kernel serve (ws://127.0.0.1:%SERVE_PORT%) ...
start "qi-agent serve" cmd /k "uv run python -m qi_agent.serve --port %SERVE_PORT%"

REM Wait for serve to be ready
timeout /t 2 /nobreak >nul

REM Start web app (own window)
echo [2/2] Starting web app (http://127.0.0.1:%WEB_PORT%) ...
start "qi-agent web" cmd /k "uv run python -m qi_agent.web.server --port %WEB_PORT%"

echo.
echo Started! Open http://127.0.0.1:%WEB_PORT% in your browser.
echo (Browser is not auto-opened. Close the windows to stop.)
echo.
pause
