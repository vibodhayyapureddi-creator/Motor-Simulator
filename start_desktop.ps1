<#
  Desktop-style launcher: starts the local server, then opens the app in
  its own window (Edge/Chrome --app mode: no tabs, no address bar) so it
  feels like a native application. Zero extra dependencies - this is the
  lightweight packaging path; a full Tauri/Electron wrap remains a
  possible future upgrade (plan phase 6).

  Launched by start_desktop.bat. Close this console window to stop the
  simulation server.
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

function Resolve-AppBrowser {
    $cands = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    return $null
}

$python = Resolve-Python
if (-not $python) {
    Write-Host "ERROR: no working Python 3 found. Run setup_windows.bat first." -ForegroundColor Red
    exit 1
}

$hostAddr = if ($env:MSP_HOST) { $env:MSP_HOST } else { '127.0.0.1' }
$port     = if ($env:MSP_PORT) { $env:MSP_PORT } else { '8765' }
$url = "http://${hostAddr}:${port}/"

Write-Host "Motor Test Bench (desktop window)"
Write-Host "  Python : $python"
Write-Host "  URL    : $url"
Write-Host "  Close this console window to stop the simulation server."
Write-Host ""

$env:PYTHONPATH = Join-Path $root 'python'
$server = Start-Process -FilePath $python `
    -ArgumentList '-m', 'motorsim_server', '--host', $hostAddr, '--port', $port `
    -NoNewWindow -PassThru

# wait for the port to come up (max ~10 s)
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    try {
        $probe = [System.Net.Sockets.TcpClient]::new()
        $probe.Connect($hostAddr, [int]$port)
        $probe.Close()
        break
    } catch { }
}

$browser = Resolve-AppBrowser
if ($browser) {
    Start-Process -FilePath $browser -ArgumentList "--app=$url", "--window-size=1500,950"
} else {
    Write-Host "No Edge/Chrome found for app mode - opening the default browser."
    Start-Process $url
}

try { Wait-Process -Id $server.Id } catch { }
