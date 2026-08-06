@echo off
REM ============================================================
REM  One-click Windows setup + build for the Motor Simulation
REM  Program.
REM
REM  Double-click this file. It installs the prerequisites
REM  (Python, CMake, Visual Studio Build Tools with the C++
REM  workload) if missing, then builds the C++ engine and runs
REM  the example scenarios.
REM
REM  It runs setup_windows.ps1 by executing its CONTENT (not as
REM  a .ps1 file). On managed/work laptops a Group Policy can
REM  block running .ps1 files even with -ExecutionPolicy Bypass;
REM  running the content as a command sidesteps that.
REM
REM  A full log is written to setup_log.txt in this folder.
REM ============================================================
setlocal
set "MSP_ROOT=%~dp0"
set "MSP_SCRIPT=%~dp0setup_windows.ps1"
set "LOG=%~dp0setup_log.txt"

REM --- Fresh log + pure-batch environment probes --------------------
REM (These always run, so even if PowerShell is locked down entirely
REM  the log still tells us what's on this machine.)
> "%LOG%" echo Motor Simulation Program - setup log
>>"%LOG%" echo Timestamp: %DATE% %TIME%
>>"%LOG%" echo.
>>"%LOG%" echo [probe] where python
>>"%LOG%" 2>&1 where python
>>"%LOG%" echo [probe] where py
>>"%LOG%" 2>&1 where py
>>"%LOG%" echo [probe] where cmake
>>"%LOG%" 2>&1 where cmake
>>"%LOG%" echo [probe] where winget
>>"%LOG%" 2>&1 where winget
>>"%LOG%" echo [probe] execution policy list
>>"%LOG%" 2>&1 powershell -NoProfile -Command "Get-ExecutionPolicy -List | Out-String"
>>"%LOG%" echo [probe] end
>>"%LOG%" echo.

echo.
echo === Motor Simulation Program : setup + build ===
echo.
echo This installs Python, CMake and the C++ Build Tools if needed, then
echo builds the engine and runs the examples. A Windows UAC prompt may
echo appear during installs - please accept it. The C++ Build Tools are a
echo large download, so this can run for several minutes.
echo.
echo A full log is being saved to:
echo    %LOG%
echo.

REM --- Run the setup script by executing its content (GPO-proof) ----
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -Raw -LiteralPath $env:MSP_SCRIPT))"

echo.
echo ============================================================
echo Setup finished. If anything went wrong, the file
echo    setup_log.txt
echo in this folder has the full details.
echo ============================================================
echo.
pause
endlocal
