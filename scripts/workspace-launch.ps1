param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$sfRoot = Split-Path -Parent $PSScriptRoot
$sfPython = Join-Path $sfRoot 'apps/api/.workspace-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $sfPython)) { $sfPython = Join-Path $sfRoot 'apps/api/.venv/Scripts/python.exe' }
$sfNode = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $sfNode) { throw 'Install Node.js and make node.exe available on PATH.' }
Set-Location -LiteralPath $sfRoot
if ($Stop) {
    & $sfPython -B -I (Join-Path $sfRoot 'scripts/preview_launcher.py') stop
    exit $LASTEXITCODE
}
$sfRuntime = Join-Path $sfRoot 'q/pv/runtime.json'
$sfRunning = $false
if (Test-Path -LiteralPath $sfRuntime) {
    $sfState = Get-Content -LiteralPath $sfRuntime -Raw | ConvertFrom-Json
    if ($sfState.worktree -eq $sfRoot -and $sfState.phase -eq 'running') {
        try {
            $sfReady = Invoke-RestMethod -Uri 'http://127.0.0.1:8146/api/v1/preview/runtime' -TimeoutSec 3
            $sfRunning = $sfReady.instance -eq $sfState.instance
        } catch { $sfRunning = $false }
    }
}
if (-not $sfRunning) {
    & $sfPython -B -I (Join-Path $sfRoot 'scripts/preview_launcher.py') start --api-port 8146 --web-port 3146
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Review q/pv/l startup logs. Existing data is preserved.' }
}
Write-Host 'Quantelune workspace: http://127.0.0.1:3146'
Write-Host 'Local project data: ' (Join-Path $sfRoot 'q/pv')
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:3146' }
