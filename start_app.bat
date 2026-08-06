@echo off
REM ============================================================
REM  Motor Test Bench - interactive simulator (one click).
REM
REM  Starts the local web server (Python standard library only,
REM  nothing to install) and opens the app in your browser.
REM  Close this window or press Ctrl+C to stop the server.
REM ============================================================
setlocal
set "MSP_ROOT=%~dp0"
set "MSP_SCRIPT=%~dp0start_app.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -Raw -LiteralPath $env:MSP_SCRIPT))"
echo.
pause
endlocal
