[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Apk,
    [string]$Serial,
    [string]$AndroidSdk = "$env:LOCALAPPDATA\Android\Sdk",
    [switch]$Launch
)
$ErrorActionPreference = 'Stop'
$adb = Join-Path $AndroidSdk 'platform-tools\adb.exe'
$apkPath = (Resolve-Path -LiteralPath $Apk).Path
$signingPath = "$apkPath.signing-info.json"
if (-not (Test-Path -LiteralPath $signingPath)) {
    $signingPath = Join-Path (Split-Path $apkPath -Parent) 'release-info.json'
}
if (-not (Test-Path -LiteralPath $signingPath)) { throw 'Signing metadata is missing; keep release-info.json with downloaded APKs.' }
$signing = Get-Content -LiteralPath $signingPath -Raw | ConvertFrom-Json
if (($signing.PSObject.Properties.Name -contains 'signed') -and -not $signing.signed) { throw 'Release metadata describes an unsigned APK.' }
if ($signing.apk -ne [IO.Path]::GetFileName($apkPath) -or
    $signing.apkSha256 -ne (Get-FileHash -LiteralPath $apkPath -Algorithm SHA256).Hash.ToLowerInvariant()) {
    throw 'APK checksum does not match its signing metadata.'
}
$devices = & $adb devices
if ($LASTEXITCODE -ne 0) { throw 'ADB device detection failed.' }
$connected = @($devices | Where-Object { $_ -match '^\S+\s+device$' } | ForEach-Object { ($_ -split '\s+')[0] })
if (-not $Serial) {
    if ($connected.Count -ne 1) { throw 'Specify -Serial; exactly one authorized device is required for automatic selection.' }
    $Serial = $connected[0]
}
if ($Serial -notin $connected) { throw "Device is unavailable or has not authorized ADB: $Serial" }
& $adb -s $Serial shell getprop ro.product.model
& $adb -s $Serial install -r $apkPath
if ($LASTEXITCODE -ne 0) { throw 'APK installation failed. Existing app data was not removed.' }
& $adb -s $Serial shell dumpsys package net.mullvad.mullvadvpn | Select-String -Pattern 'versionCode=|versionName=|lastUpdateTime='
if ($Launch) {
    $activity = & $adb -s $Serial shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER net.mullvad.mullvadvpn
    $component = @($activity | Where-Object { $_ -match '^net\.mullvad\.mullvadvpn/' })
    if ($component.Count -ne 1) { throw 'Could not resolve launcher activity.' }
    & $adb -s $Serial shell am start -W -n $component[0]
    if ($LASTEXITCODE -ne 0) { throw 'App launch failed.' }
}
