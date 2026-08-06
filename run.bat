@echo off
REM ============================================================
REM  Run a single motor scenario from a JSON config.
REM
REM  Three ways to use it:
REM    1. Double-click run.bat  -> it lists the available configs
REM       and asks which one to run.
REM    2. Drag a .json config file onto run.bat.
REM    3. From a terminal:  run.bat python\configs\dc_motor_basic.json
REM
REM  Output (CSV + PNG plot) goes to the "output" folder.
REM ============================================================
setlocal
set "MSP_ROOT=%~dp0"
set "MSP_SCRIPT=%~dp0run_config.ps1"
set "MSP_CONFIG=%~1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -Raw -LiteralPath $env:MSP_SCRIPT))"
echo.
pause
endlocal
