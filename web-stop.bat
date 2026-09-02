@echo off
REM ============================================================
REM  qi-agent Web Shell one-click stop (kill serve + web)
REM  Usage: double-click, or: web-stop.bat
REM ============================================================
setlocal

echo ============================================
echo  qi-agent Web Shell stop
echo ============================================

REM Find and kill the web app process (port 9000)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9000" ^| findstr "LISTENING"') do (
    echo [kill] web app PID %%a (port 9000)
    taskkill /PID %%a /F >nul 2>&1
)

REM Find and kill the kernel serve process (port 8765)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
    echo [kill] kernel serve PID %%a (port 8765)
    taskkill /PID %%a /F >nul 2>&1
)

REM Also kill any lingering qi-agent python processes (safety net)
taskkill /IM python.exe /FI "WINDOWTITLE eq qi-agent*" /F >nul 2>&1

echo.
echo Done. If any process still listens, check:
echo   netstat -ano ^| findstr ":9000 :8765"
echo.
pause
