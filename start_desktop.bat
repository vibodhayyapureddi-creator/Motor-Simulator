@echo off
REM ============================================================
REM  Motor Test Bench - desktop window (one click).
REM
REM  Starts the local server and opens the app in its own window
REM  (Edge/Chrome app mode - no tabs or address bar), so it feels
REM  like a native application. Close this console to stop it.
REM ============================================================
setlocal
set "MSP_ROOT=%~dp0"
set "MSP_SCRIPT=%~dp0start_desktop.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -Raw -LiteralPath $env:MSP_SCRIPT))"
endlocal
