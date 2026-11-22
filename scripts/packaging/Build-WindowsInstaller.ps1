# Build a self-contained Windows installer from prepared, local inputs.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$version = (Get-Content -Raw -LiteralPath (Join-Path $repo 'apps/desktop/src-tauri/tauri.conf.json') | ConvertFrom-Json).version
$local = Join-Path $repo '.local'
$runtime = Join-Path $local 'release-base/runtime'
$base = Join-Path $local 'release-base'
$sidecar = Join-Path $local 'sidecar/dist/statforge-sidecar'
$stage = Join-Path $local ('setup-stages/' + [Guid]::NewGuid().ToString('N'))
$target = Join-Path $local 'setup-target'
if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).LinkType -eq 'Junction') {
    $target = (Get-Item -LiteralPath $target).Target
}
$binary = Join-Path $repo 'apps/desktop/src-tauri/binaries/statforge-sidecar-x86_64-pc-windows-msvc.exe'
foreach ($required in @($runtime, (Join-Path $sidecar '_internal'), (Join-Path $sidecar 'statforge-sidecar.exe'), (Join-Path $base 'media-runtime.json'), (Join-Path $base 'THIRD_PARTY.txt'), (Join-Path $repo 'apps/web/out/index.html'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required prepared input missing: $required" }
}
$sidecarReceipt = Join-Path $local 'setup-sidecar.sha256'
if (Test-Path -LiteralPath $binary) {
    if (-not (Test-Path -LiteralPath $sidecarReceipt)) { throw 'Existing staged sidecar is not owned by this builder; preserve it.' }
    $previousHash = (Get-Content -LiteralPath $sidecarReceipt -Raw).Trim()
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $binary).Hash -ne $previousHash) { throw 'Existing staged sidecar changed; preserve it.' }
}
New-Item -ItemType Directory -Path $stage, $target, (Split-Path $binary) -Force | Out-Null
$env:CARGO_TARGET_DIR = $target
$env:TEMP = Join-Path $local 'setup-temp'; $env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $local 'setup-npm-cache'
$env:UV_CACHE_DIR = Join-Path $local 'setup-uv-cache'
$env:PIP_CACHE_DIR = Join-Path $local 'setup-pip-cache'
$env:PNPM_HOME = Join-Path $local 'setup-pnpm-home'
$env:COREPACK_HOME = Join-Path $local 'setup-corepack'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
Remove-Item Env:STATFORGE_LOCAL_SIDECAR_PYTHON -ErrorAction SilentlyContinue
$env:NEXT_TELEMETRY_DISABLED = '1'
Copy-Item -LiteralPath (Join-Path $sidecar 'statforge-sidecar.exe') -Destination $binary -Force
[IO.File]::WriteAllText($sidecarReceipt, (Get-FileHash -Algorithm SHA256 -LiteralPath $binary).Hash, [Text.Encoding]::ASCII)
# Stream inputs into the archive; do not duplicate the 2.7 GiB tree in staging.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipPath = Join-Path $stage 'quantelune-runtime.zip'
$manifestFiles = [Collections.Generic.List[object]]::new()
$zip = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($entry in @(@{source=$runtime; name='runtime'}, @{source=(Join-Path $sidecar '_internal'); name='_internal'})) {
        $source = [IO.Path]::GetFullPath($entry.source).TrimEnd('\')
        foreach ($directory in Get-ChildItem -LiteralPath $source -Directory -Recurse -Force) {
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse input rejected: $($directory.FullName)" }
        }
        foreach ($file in Get-ChildItem -LiteralPath $source -File -Recurse -Force) {
            if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse input rejected: $($file.FullName)" }
            $relative = $file.FullName.Substring($source.Length).TrimStart('\').Replace('\','/')
            $archivePath = $entry.name + '/' + $relative
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $archivePath, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
            $manifestFiles.Add(@{path=$archivePath; bytes=$file.Length; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash})
        }
    }
} finally { $zip.Dispose() }
$manifestPath = Join-Path $stage 'runtime-manifest.json'
$manifest = @{version=$version; sourceHead=(& git -C $repo rev-parse HEAD); files=$manifestFiles}
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$resources = @{}
$resources[$manifestPath.Replace('\','/')] = 'runtime-manifest.json'
$resources[$zipPath.Replace('\','/')] = 'quantelune-runtime.zip'
$resources[(Join-Path $repo 'apps/desktop/src-tauri/windows/extract-runtime.ps1').Replace('\','/')] = 'extract-runtime.ps1'
foreach ($name in @('media-runtime.json','THIRD_PARTY.txt')) { $resources[(Join-Path $base $name).Replace('\','/')] = $name }
$resources[(Join-Path $repo 'LICENSE').Replace('\','/')] = 'LICENSE'
$config = @{ build=@{beforeBuildCommand=$null}; bundle=@{useLocalToolsDir=$true; resources=$resources; windows=@{webviewInstallMode=@{type='downloadBootstrapper'; silent=$true}}} }
$configPath = Join-Path $stage 'tauri-setup.json'
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
$cli = Join-Path $repo 'apps/desktop/node_modules/@tauri-apps/cli/tauri.js'
if (-not (Test-Path -LiteralPath $cli)) { throw "Install locked workspace dependencies before building: $cli" }
Push-Location (Join-Path $repo 'apps/desktop')
try {
    $buildStarted = Get-Date
    & node $cli build --bundles nsis --config $configPath
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed: $LASTEXITCODE" }
} finally { Pop-Location }
$installers = @(Get-ChildItem -LiteralPath (Join-Path $target 'release/bundle/nsis') -Filter "*${version}*x64*setup.exe" | Where-Object { $_.LastWriteTime -ge $buildStarted })
if ($installers.Count -ne 1) { throw 'Expected exactly one NSIS installer.' }
$output = Join-Path $stage "Quantelune-$version-Setup.exe"
try {
    New-Item -ItemType HardLink -Path $output -Target $installers[0].FullName -ErrorAction Stop | Out-Null
} catch {
    # An installer staged on a different volume cannot be hard-linked.
    if (Test-Path -LiteralPath $output) { throw }
    Copy-Item -LiteralPath $installers[0].FullName -Destination $output
}
Write-Output "Installer: $output"
Get-FileHash -Algorithm SHA256 -LiteralPath $output
