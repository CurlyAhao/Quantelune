param([ValidateSet('All','Web','Api')][string]$Part='All')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$local = Join-Path $root '.local'
$env:TEMP = Join-Path $local 'tmp'
$env:TMP = $env:TEMP
$env:XDG_CACHE_HOME = Join-Path $local 'cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $env:XDG_CACHE_HOME 'pyinstaller'
$env:MPLCONFIGDIR = Join-Path $env:XDG_CACHE_HOME 'matplotlib'
$env:NUMBA_CACHE_DIR = Join-Path $env:XDG_CACHE_HOME 'numba'
$env:UV_CACHE_DIR = Join-Path $local 'uv-cache'
$env:npm_config_cache = Join-Path $local 'npm-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:STATFORGE_SIDECAR_LAYOUT = 'onedir'
$env:STATFORGE_COMPACT_BUILD = '1'
$env:NEXT_TELEMETRY_DISABLED = '1'
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:XDG_CACHE_HOME | Out-Null
if ($Part -in @('All','Web')) {
    Push-Location (Join-Path $root 'apps/web')
    try {
        & node node_modules/next/dist/bin/next build
        if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
    } finally { Pop-Location }
}
if ($Part -in @('All','Api')) {
    Push-Location (Join-Path $root 'apps/api')
    try {
        & (Join-Path $root 'apps/api/.venv/Scripts/python.exe') -B -m PyInstaller --noconfirm --workpath (Join-Path $local 'sidecar/work') --distpath (Join-Path $local 'sidecar/dist') statforge-sidecar.spec
        if ($LASTEXITCODE -ne 0) { throw 'Backend build failed.' }
    } finally { Pop-Location }
}
if ($Part -eq 'All') { & (Join-Path $PSScriptRoot 'Build-WindowsInstaller.ps1') }
