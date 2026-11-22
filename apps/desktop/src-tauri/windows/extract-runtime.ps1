param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [ValidateSet('Install','Uninstall')][string]$Mode = 'Install'
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') { $env:PSModulePath = Join-Path $PSHOME 'Modules' }
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$record = Join-Path $root 'quantelune-installed-files.json'
$stage = $null
function Resolve-ManagedPath([string]$relative) {
    if ($relative -notmatch '^(runtime|_internal)/' -or $relative.Contains('\') -or $relative -match '(^|/)\.\.?(/|$)' -or $relative.Contains(':')) { throw "Invalid managed path: $relative" }
    $path = [IO.Path]::GetFullPath((Join-Path $root $relative.Replace('/','\')))
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escapes installation directory.' }
    $cursor = $path
    while ($cursor -and $cursor.Length -gt $root.Length) {
        if ([IO.Directory]::Exists($cursor) -or [IO.File]::Exists($cursor)) {
            if ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point in managed path: $cursor" }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $path
}
function Read-Record {
    if (-not (Test-Path -LiteralPath $record)) { return @() }
    $data = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    return @($data.files)
}
function Remove-OwnedFile($entry) {
    $path = Resolve-ManagedPath $entry.path
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -eq $entry.sha256) { Remove-Item -LiteralPath $path -Force }
    }
}
function Remove-EmptyDirectories {
    foreach ($name in @('runtime','_internal')) {
        $directory = Join-Path $root $name
        if (Test-Path -LiteralPath $directory -PathType Container) {
            if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            $dirs = @(Get-ChildItem -LiteralPath $directory -Directory -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending)
            foreach ($item in $dirs) {
                if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { try { [IO.Directory]::Delete($item.FullName, $false) } catch [IO.IOException] {} }
            }
            try { [IO.Directory]::Delete($directory, $false) } catch [IO.IOException] {}
        }
    }
}
try {
    $old = @(Read-Record)
    if ($Mode -eq 'Uninstall') {
        foreach ($item in $old) { Remove-OwnedFile $item }
        Remove-EmptyDirectories
        if (Test-Path -LiteralPath $record) { Remove-Item -LiteralPath $record -Force }
        exit 0
    }
    $new = Get-Content -LiteralPath (Join-Path $root 'runtime-manifest.json') -Raw | ConvertFrom-Json
    $owned = @{}
    foreach ($item in $old) { $null = Resolve-ManagedPath $item.path; $owned[$item.path] = $item.sha256 }
    $seen = @{}
    foreach ($item in @($new.files)) {
        $path = Resolve-ManagedPath $item.path
        if ($seen.ContainsKey($item.path)) { throw "Duplicate manifest path: $($item.path)" }
        $seen[$item.path] = $true
        if (Test-Path -LiteralPath $path) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or -not $owned.ContainsKey($item.path) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $owned[$item.path]) { throw "Existing file is not an unchanged managed file: $path" }
        }
    }
    $stage = Join-Path $root ('.quantelune-stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $root 'quantelune-runtime.zip'), $stage)
    foreach ($item in @($new.files)) {
        $source = Join-Path $stage $item.path.Replace('/','\')
        if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash -ne $item.sha256) { throw "Archive verification failed: $($item.path)" }
    }
    foreach ($required in @('runtime/python/python.exe','runtime/ffmpeg/ffmpeg.exe','_internal')) {
        if (-not (Test-Path -LiteralPath (Join-Path $stage $required))) { throw "Missing runtime component: $required" }
    }
    $fresh = $old.Count -eq 0 -and !(Test-Path -LiteralPath (Join-Path $root 'runtime')) -and !(Test-Path -LiteralPath (Join-Path $root '_internal'))
    if ($fresh) {
        foreach ($name in @('runtime','_internal')) {
            [IO.Directory]::Move((Join-Path $stage $name),(Join-Path $root $name))
        }
    } else {
        foreach ($item in $old) { Remove-OwnedFile $item }
        foreach ($item in @($new.files)) {
            $destination = Resolve-ManagedPath $item.path
            if (Test-Path -LiteralPath $destination) { throw "Destination was not cleared: $destination" }
            $parent = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            Move-Item -LiteralPath (Join-Path $stage $item.path.Replace('/','\')) -Destination $destination
        }
    }
    [IO.File]::WriteAllText($record, ($new | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Remove-Item -LiteralPath (Join-Path $root 'quantelune-runtime.zip') -Force
    exit 0
} catch {
    $detail = $_.Exception.Message + " at " + $_.ScriptStackTrace
    [IO.File]::WriteAllText((Join-Path $root 'quantelune-setup-error.log'), $detail)
    [Console]::Error.WriteLine($detail)
    exit 1
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
