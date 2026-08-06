<#
  Motor Simulation Program - Windows one-shot setup + build (self-contained).

  Normally launched by setup_windows.bat, which runs this file's CONTENT via
  Invoke-Expression rather than as a .ps1 file, so a managed-machine Group
  Policy execution policy can't block it.

  Installs any missing prerequisites (a real Python 3, CMake, Visual Studio
  Build Tools with the C++ workload) via winget, then builds the
  C++ / pybind11 engine, verifies it loads, and runs the example scenarios.
  Everything is logged to setup_log.txt in the project folder.
#>

# --- Locate the project root ----------------------------------------
$root = $env:MSP_ROOT
if (-not $root) { try { $root = Split-Path -Parent $MyInvocation.MyCommand.Path } catch { } }
if (-not $root) { $root = (Get-Location).Path }
$root = $root.TrimEnd('\')
Set-Location $root

$log = Join-Path $root 'setup_log.txt'
try { Start-Transcript -Path $log -Append -ErrorAction SilentlyContinue | Out-Null } catch { }

$ErrorActionPreference = 'Continue'

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Line { Write-Host ("-" * 60) }

# Return the path to a REAL python (never the Microsoft Store alias stub),
# validated by actually running it. Checks PATH entries (minus WindowsApps),
# uv-managed pythons, and standard python.org install locations.
function Resolve-Python {
    $cands = New-Object System.Collections.Generic.List[string]
    foreach ($name in 'python', 'python3', 'py') {
        Get-Command $name -All -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.Source -and ($_.Source -notmatch 'WindowsApps')) { $cands.Add($_.Source) }
        }
    }
    foreach ($glob in @(
            "$env:USERPROFILE\.local\bin\python*.exe",
            "$env:APPDATA\uv\python\*\python.exe",
            "$env:LOCALAPPDATA\uv\python\*\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe")) {
        Get-ChildItem $glob -ErrorAction SilentlyContinue | ForEach-Object { $cands.Add($_.FullName) }
    }
    foreach ($c in $cands) {
        try {
            $v = & $c --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3') { return $c }
        } catch { }
    }
    return $null
}

Line
Write-Host "Motor Simulation Program : setup + build"
Write-Host ("PowerShell version : {0}" -f $PSVersionTable.PSVersion)
Write-Host ("Language mode      : {0}" -f $ExecutionContext.SessionState.LanguageMode)
Write-Host ("Project root       : {0}" -f $root)
Line

if (-not (Have 'winget')) {
    Write-Host "[ERROR] winget (the 'App Installer') was not found." -ForegroundColor Red
    Write-Host "        Install 'App Installer' from the Microsoft Store, then re-run."
    try { Stop-Transcript | Out-Null } catch { }
    return
}

# --- Python (real interpreter, not the Store stub) ------------------
$PY = Resolve-Python
if (-not $PY) {
    Write-Host "No real Python found; installing Python 3 via winget..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 --source winget -e --accept-source-agreements --accept-package-agreements 2>&1 |
        ForEach-Object { Write-Host $_ }
    Refresh-Path
    $PY = Resolve-Python
}
if (-not $PY) {
    Write-Host "[ERROR] Could not find a working Python after install." -ForegroundColor Red
    Write-Host "        Close this window, open a NEW one, and re-run setup_windows.bat."
    try { Stop-Transcript | Out-Null } catch { }
    return
}
Write-Host ("Using Python : {0}" -f $PY)
Write-Host ("             : {0}" -f (& $PY --version 2>&1))

# --- CMake -----------------------------------------------------------
if (Have 'cmake') {
    Write-Host ("CMake present : {0}" -f (cmake --version | Select-Object -First 1))
} else {
    Write-Host "Installing CMake (winget)..." -ForegroundColor Yellow
    winget install --id Kitware.CMake --source winget -e --accept-source-agreements --accept-package-agreements 2>&1 |
        ForEach-Object { Write-Host $_ }
    Refresh-Path
}
if (-not (Have 'cmake')) {
    Write-Host "[ERROR] CMake still not on PATH. Close this window, open a NEW one, re-run." -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    return
}

# --- Visual Studio Build Tools / C++ compiler -----------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$haveVC  = $false
if (Test-Path $vswhere) {
    $vc = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vc) { $haveVC = $true; Write-Host ("C++ compiler present : {0}" -f $vc) }
}
if (-not $haveVC) {
    Write-Host "Installing Visual Studio Build Tools 2022 (C++ workload)..." -ForegroundColor Yellow
    Write-Host "(LARGE download -- may run for many minutes and show a UAC prompt.)"
    winget install --id Microsoft.VisualStudio.2022.BuildTools --source winget -e `
        --accept-source-agreements --accept-package-agreements `
        --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" 2>&1 |
        ForEach-Object { Write-Host $_ }
    Refresh-Path
}

# --- Python dependencies (best-effort; not required for the build) --
Line
Write-Host "Installing Python dependencies (pybind11, matplotlib) -- optional..."
Set-Location $root
& $PY -m pip --version 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip not available; bootstrapping it with ensurepip..."
    & $PY -m ensurepip --upgrade 2>&1 | ForEach-Object { Write-Host $_ }
}
& $PY -m pip install -r requirements.txt 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Some Python deps didn't install (matplotlib is only needed for plots;" -ForegroundColor Yellow
    Write-Host "       the simulation and CSV output work without it)."
}

# --- Configure + build ----------------------------------------------
# Hand CMake the SAME interpreter we'll run with, so the compiled
# extension's ABI matches (pybind11 builds against this Python).
Line
Write-Host "Configuring CMake (engine + Python bindings)..."
& cmake -S . -B build -DMOTORSIM_BUILD_PYTHON_BINDINGS=ON -DMOTORSIM_BUILD_DEMO=ON `
    "-DPYTHON_EXECUTABLE=$PY" "-DPython_EXECUTABLE=$PY" "-DPython3_EXECUTABLE=$PY" 2>&1 |
    ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] CMake configure failed (exit $LASTEXITCODE)." -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    return
}

Write-Host "Building (Release)..."
& cmake --build build --config Release 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Build failed (exit $LASTEXITCODE). See the compiler output above." -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    return
}

# --- Verify + run examples ------------------------------------------
Line
Write-Host "Verifying the compiled engine loads..."
Push-Location (Join-Path $root 'python')
& $PY -c "import motorsim_py; print('  motorsim_py imported OK')" 2>&1 | ForEach-Object { Write-Host $_ }
& $PY -c "import sys; sys.path.insert(0,'.'); from motorsim_app import engine_bridge as e; print('  active backend:', e.BACKEND_NAME)" 2>&1 | ForEach-Object { Write-Host $_ }

$outDir = Join-Path $root 'output'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
Write-Host "Running example scenarios..."
& $PY -m motorsim_app.cli --config configs\dc_motor_basic.json --out-dir $outDir 2>&1 | ForEach-Object { Write-Host $_ }
& $PY -m motorsim_app.cli --config configs\bldc_motor_basic.json --out-dir $outDir 2>&1 | ForEach-Object { Write-Host $_ }
Pop-Location

Line
Write-Host "=== SETUP COMPLETE ===" -ForegroundColor Green
Write-Host ("Python used     : {0}" -f $PY)
Write-Host ("Outputs (CSV/PNG): {0}" -f $outDir)
Write-Host ("Full log        : {0}" -f $log)
Write-Host ""
Write-Host "To run scenarios again later, double-click run_examples.bat."
try { Stop-Transcript | Out-Null } catch { }
