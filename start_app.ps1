<#
  Launches the interactive Motor Test Bench (plan sections 9 / 11 phase 6):
  starts the local web server with a REAL Python interpreter (never the
  Microsoft Store stub) and opens the app in the default browser.
  Launched by start_app.bat; can also be run directly.

  Optional env overrides: MSP_HOST (default 127.0.0.1), MSP_PORT (8765).
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

$python = Resolve-Python
if (-not $python) {
    Write-Host "ERROR: no working Python 3 found. Run setup_windows.bat first." -ForegroundColor Red
    exit 1
}

$hostAddr = if ($env:MSP_HOST) { $env:MSP_HOST } else { '127.0.0.1' }
$port     = if ($env:MSP_PORT) { $env:MSP_PORT } else { '8765' }

Write-Host "Motor Test Bench"
Write-Host "  Python : $python"
Write-Host "  URL    : http://${hostAddr}:${port}/"
Write-Host "  (server uses only the Python standard library - nothing to install)"
Write-Host ""

$env:PYTHONPATH = Join-Path $root 'python'
& $python -m motorsim_server --host $hostAddr --port $port --open-browser
exit $LASTEXITCODE
