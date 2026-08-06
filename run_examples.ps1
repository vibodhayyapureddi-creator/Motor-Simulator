<#
  Runs both example motor scenarios using a REAL Python interpreter (never
  the Microsoft Store alias stub), writing CSV + PNG output to output\.

  Launched by run_examples.bat. Works whether or not the C++ extension is
  built: without motorsim_py the CLI uses the pure-Python fallback engine.
#>
$root = $env:MSP_ROOT
if (-not $root) { try { $root = Split-Path -Parent $MyInvocation.MyCommand.Path } catch { } }
if (-not $root) { $root = (Get-Location).Path }
$root = $root.TrimEnd('\')

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

$PY = Resolve-Python
if (-not $PY) {
    Write-Host "[ERROR] No real Python found. Run setup_windows.bat first." -ForegroundColor Red
    return
}
Write-Host ("Using Python : {0}" -f $PY)

$outDir = Join-Path $root 'output'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

Push-Location (Join-Path $root 'python')
Write-Host "=== DC motor scenario ==="
& $PY -m motorsim_app.cli --config configs\dc_motor_basic.json --out-dir $outDir 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "=== BLDC motor scenario ==="
& $PY -m motorsim_app.cli --config configs\bldc_motor_basic.json --out-dir $outDir 2>&1 | ForEach-Object { Write-Host $_ }
Pop-Location

Write-Host ""
Write-Host ("Done. Outputs (CSV + PNG) are in: {0}" -f $outDir)
