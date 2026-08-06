<#
  Runs a single motor scenario from a JSON config, using a REAL Python
  interpreter (never the Microsoft Store stub). Launched by run.bat.

  The config to run comes in via the MSP_CONFIG environment variable. It may
  be:
    * a full path to a .json file, or
    * just a config file name that lives in python\configs\, or
    * empty -- in which case the available configs are listed and you're
      prompted to pick one.
  Output (CSV + PNG) is written to the output\ folder.
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

$configsDir = Join-Path $root 'python\configs'
$config = $env:MSP_CONFIG

# If nothing was passed, list the available configs and ask.
if (-not $config) {
    Write-Host "Available scenario configs in python\configs:"
    Get-ChildItem (Join-Path $configsDir '*.json') -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ("   {0}" -f $_.Name) }
    Write-Host ""
    $config = Read-Host "Enter a config file name (or full path)"
}

# Resolve the config to an absolute path.
$configPath = $null
if (Test-Path -LiteralPath $config) {
    $configPath = (Resolve-Path -LiteralPath $config).Path
} elseif (Test-Path -LiteralPath (Join-Path $configsDir $config)) {
    $configPath = (Resolve-Path -LiteralPath (Join-Path $configsDir $config)).Path
}
if (-not $configPath) {
    Write-Host ("[ERROR] Could not find config '{0}'." -f $config) -ForegroundColor Red
    Write-Host ("        Look in {0} for the available files." -f $configsDir)
    return
}

$PY = Resolve-Python
if (-not $PY) {
    Write-Host "[ERROR] No real Python found. Run setup_windows.bat first." -ForegroundColor Red
    return
}

$outDir = Join-Path $root 'output'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

Write-Host ("Python : {0}" -f $PY)
Write-Host ("Config : {0}" -f $configPath)
Write-Host ""
Push-Location (Join-Path $root 'python')
& $PY -m motorsim_app.cli --config $configPath --out-dir $outDir 2>&1 | ForEach-Object { Write-Host $_ }
Pop-Location
Write-Host ""
Write-Host ("Output (CSV + PNG) is in: {0}" -f $outDir)
