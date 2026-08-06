@echo off
REM ============================================================
REM  Motor Simulation Program - Windows build script
REM
REM  Builds the C++ engine and the pybind11 Python extension
REM  (motorsim_py) so the Python control layer runs on the real
REM  compiled engine instead of the pure-Python fallback.
REM
REM  Just double-click this file, or run it from a terminal.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo === Motor Simulation Program : build ===
echo.

REM --- 1. Check Python ----------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on your PATH.
    echo         Install Python 3 from https://www.python.org/downloads/
    echo         During install, tick "Add python.exe to PATH".
    goto :fail
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo Found %%v

REM --- 2. Check CMake -----------------------------------------
where cmake >nul 2>&1
if errorlevel 1 (
    echo [ERROR] CMake was not found on your PATH.
    echo         Install it with:  winget install Kitware.CMake
    echo         or download from https://cmake.org/download/
    goto :fail
)
for /f "delims=" %%v in ('cmake --version 2^>^&1 ^| findstr /i "version"') do echo Found %%v

REM --- 3. Install Python dependencies -------------------------
echo.
echo Installing Python dependencies (pybind11, matplotlib)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    goto :fail
)

REM --- 4. Configure -------------------------------------------
echo.
echo Configuring CMake (this step also needs a C++ compiler)...
cmake -S . -B build -DMOTORSIM_BUILD_PYTHON_BINDINGS=ON -DMOTORSIM_BUILD_DEMO=ON
if errorlevel 1 (
    echo.
    echo [ERROR] CMake configure failed.
    echo         The most common cause is a missing C++ compiler.
    echo         Install "Visual Studio Build Tools 2022" with the
    echo         "Desktop development with C++" workload:
    echo             winget install Microsoft.VisualStudio.2022.BuildTools
    echo         then re-run this script.
    goto :fail
)

REM --- 5. Build -----------------------------------------------
echo.
echo Building (Release)...
cmake --build build --config Release
if errorlevel 1 (
    echo [ERROR] Build failed. See the compiler output above.
    goto :fail
)

REM --- 6. Verify ----------------------------------------------
echo.
echo Verifying the compiled engine loads...
pushd python
python -c "import motorsim_py; print('  motorsim_py imported OK')"
if errorlevel 1 (
    echo [WARN] Build succeeded but motorsim_py could not be imported.
    echo        Check that motorsim_py.pyd landed directly in the python\ folder.
    popd
    goto :fail
)
python -c "import sys; sys.path.insert(0,'.'); from motorsim_app import engine_bridge as e; print('  active backend:', e.BACKEND_NAME)"
popd

echo.
echo === BUILD SUCCEEDED ===
echo.
echo Run a simulation with:
echo     cd python
echo     python -m motorsim_app.cli --config configs\dc_motor_basic.json
echo.
echo ...or just double-click run_examples.bat
echo.
pause
goto :end

:fail
echo.
echo Build did not complete. See the message above.
echo.
pause
exit /b 1

:end
endlocal
