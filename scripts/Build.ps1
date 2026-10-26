[CmdletBinding()]
param(
    [string]$Source,
    [string]$Repository = 'https://github.com/zerox80/mullvadvpn-app.git',
    [string]$Ref,
    [ValidateSet('arm64', 'all')][string]$Architecture = 'arm64',
    [string]$OutputDirectory,
    [ValidateRange(1, 32)][int]$Workers = 4,
    [string]$Distribution = 'Ubuntu',
    [switch]$Check,
    [switch]$Offline,
    [switch]$Doctor,
    [switch]$ReuseWorkspaceCaches
)
$ErrorActionPreference = 'Stop'
if ($ReuseWorkspaceCaches -and $Architecture -ne 'arm64') {
    throw 'The existing workstation caches are for ARM64. Use default caches for -Architecture all.'
}
$builderRoot = Split-Path $PSScriptRoot -Parent

function ConvertTo-WslPath([string]$Path) {
    $converted = & wsl.exe -d $Distribution --exec wslpath -a $Path
    if ($LASTEXITCODE -ne 0) { throw "Cannot convert path for WSL: $Path" }
    return ($converted -join "`n").Trim()
}

$linuxScript = ConvertTo-WslPath (Join-Path $builderRoot 'build.sh')
$buildArguments = @('--repo', $Repository, '--arch', $Architecture, '--workers', "$Workers")
if ($Source) {
    $resolvedSource = (Resolve-Path -LiteralPath $Source).Path
    $buildArguments += @('--source', (ConvertTo-WslPath $resolvedSource))
}
if ($Ref) { $buildArguments += @('--ref', $Ref) }
if ($OutputDirectory) {
    $absoluteOutput = [IO.Path]::GetFullPath($OutputDirectory)
    [IO.Directory]::CreateDirectory($absoluteOutput) | Out-Null
    $buildArguments += @('--out', (ConvertTo-WslPath $absoluteOutput))
}
if ($Check) { $buildArguments += '--check' }
if ($Offline) { $buildArguments += '--offline' }
if ($Doctor) { $buildArguments += '--doctor' }

# These are the existing, verified caches on this workstation. Opt-in only.
$cacheVariables = @{
    SOURCE_VOLUME = 'mullvad-pause-source-9a848cd421'
    GRADLE_VOLUME = 'mullvad-pause-gradle-cache'
    CARGO_REGISTRY_VOLUME = 'mullvad-pause-cargo-registry'
    CARGO_GIT_VOLUME = 'mullvad-pause-cargo-git'
    CARGO_TARGET_VOLUME = 'mullvad-pause-cargo-target'
}
$wslArguments = @('-d', $Distribution, '--exec')
if ($ReuseWorkspaceCaches) {
    $wslArguments += 'env'
    foreach ($entry in $cacheVariables.GetEnumerator()) {
        $wslArguments += "$($entry.Key)=$($entry.Value)"
    }
}
$wslArguments += @('bash', $linuxScript)
$wslArguments += $buildArguments
& wsl.exe @wslArguments
if ($LASTEXITCODE -ne 0) { throw "Android builder failed with exit code $LASTEXITCODE." }
