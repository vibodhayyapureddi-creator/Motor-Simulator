@echo off
REM ============================================================
REM  Runs both example motor scenarios and writes their CSV +
REM  PNG output into an "output" folder next to this script.
REM
REM  Delegates to run_examples.ps1 (run via Invoke-Expression of
REM  its content, so a locked-down PowerShell execution policy
REM  can't block it) and uses your real Python, not the
REM  Microsoft Store alias stub.
REM ============================================================
setlocal
set "MSP_ROOT=%~dp0"
set "MSP_SCRIPT=%~dp0run_examples.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -Raw -LiteralPath $env:MSP_SCRIPT))"
echo.
pause
endlocal
