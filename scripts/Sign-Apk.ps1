[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Apk,
    [Parameter(Mandatory)][string]$Keystore,
    [string]$ProtectedPasswordFile,
    [Security.SecureString]$Password,
    [string]$Alias = 'release',
    [string]$OutputApk,
    [string]$AndroidSdk = "$env:LOCALAPPDATA\Android\Sdk"
)
$ErrorActionPreference = 'Stop'
$inputApk = (Resolve-Path -LiteralPath $Apk).Path
$keystorePath = (Resolve-Path -LiteralPath $Keystore).Path
if (-not $OutputApk) { $OutputApk = $inputApk -replace '-unsigned\.apk$', '.apk' }
$outputPath = [IO.Path]::GetFullPath($OutputApk)
if ($inputApk -eq $outputPath) { throw 'Input and signed APK paths must be different.' }
$buildTools = Get-ChildItem -LiteralPath (Join-Path $AndroidSdk 'build-tools') -Directory |
    Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
    Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
if (-not $buildTools) { throw 'Android SDK build-tools were not found.' }
$jbr = 'C:\Program Files\Android\Android Studio\jbr'
$originalJavaHome = $env:JAVA_HOME
if (Test-Path -LiteralPath (Join-Path $jbr 'bin\java.exe')) { $env:JAVA_HOME = $jbr }
$passwordPointer = [IntPtr]::Zero
$alignedPath = Join-Path ([IO.Path]::GetTempPath()) ("mullvad-builder-{0}.apk" -f [guid]::NewGuid())
try {
    $metadataPath = Join-Path (Split-Path $inputApk -Parent) 'build-info.json'
    $metadata = $null
    if (Test-Path -LiteralPath $metadataPath) {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($metadata.apk -ne [IO.Path]::GetFileName($inputApk) -or
            $metadata.apkSha256 -ne (Get-FileHash -LiteralPath $inputApk -Algorithm SHA256).Hash.ToLowerInvariant()) {
            throw 'APK does not match its build-info.json.'
        }
    }
    $badging = & (Join-Path $buildTools.FullName 'aapt2.exe') dump badging $inputApk
    if ($LASTEXITCODE -ne 0) { throw 'APK inspection failed.' }
    if (-not ($badging -match "^package: name='net.mullvad.mullvadvpn'")) { throw 'Unexpected application package.' }
    if ($badging -match '^application-debuggable') { throw 'Refusing a debuggable release APK.' }
    if ($ProtectedPasswordFile) {
        if ($Password) { throw 'Use either Password or ProtectedPasswordFile.' }
        $encrypted = (Get-Content -LiteralPath $ProtectedPasswordFile -Raw).Trim()
        $Password = ConvertTo-SecureString -String $encrypted
    }
    if (-not $Password) { $Password = Read-Host 'Keystore password' -AsSecureString }
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $env:MULLVAD_BUILDER_SIGN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    & (Join-Path $buildTools.FullName 'zipalign.exe') -f -P 16 4 $inputApk $alignedPath
    if ($LASTEXITCODE -ne 0) { throw 'APK alignment failed.' }
    [IO.Directory]::CreateDirectory((Split-Path $outputPath -Parent)) | Out-Null
    & (Join-Path $buildTools.FullName 'apksigner.bat') sign --ks $keystorePath --ks-pass env:MULLVAD_BUILDER_SIGN_PASSWORD --ks-key-alias $Alias --out $outputPath $alignedPath
    if ($LASTEXITCODE -ne 0) { throw 'APK signing failed.' }
    $certificates = & (Join-Path $buildTools.FullName 'apksigner.bat') verify --verbose --print-certs $outputPath
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    & (Join-Path $buildTools.FullName 'zipalign.exe') -c -P 16 4 $outputPath
    if ($LASTEXITCODE -ne 0) { throw 'Signed APK alignment verification failed.' }
    $fingerprints = @($certificates | ForEach-Object {
        if ($_ -match ' certificate SHA-256 digest: ([a-fA-F0-9]{64})$') { $Matches[1].ToLowerInvariant() }
    } | Sort-Object -Unique)
    if ($fingerprints.Count -ne 1) { throw 'Expected one verified APK signing certificate.' }
    $signingInfo = [ordered]@{
        apk = [IO.Path]::GetFileName($outputPath)
        apkSha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        certificateSha256 = $fingerprints[0]
        signedUtc = [datetime]::UtcNow.ToString('o')
        sourceCommit = $(if ($metadata) { $metadata.sourceCommit } else { $null })
    }
    $signingInfo | ConvertTo-Json | Set-Content -LiteralPath "$outputPath.signing-info.json" -Encoding utf8
    "$($signingInfo.apkSha256)  $($signingInfo.apk)" | Set-Content -LiteralPath "$outputPath.sha256" -Encoding utf8
    Write-Output "Signed and verified: $outputPath"
    Write-Output "Certificate SHA256: $($signingInfo.certificateSha256)"
} finally {
    Remove-Item Env:\MULLVAD_BUILDER_SIGN_PASSWORD -ErrorAction SilentlyContinue
    if ($passwordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer) }
    if (Test-Path -LiteralPath $alignedPath) { Remove-Item -LiteralPath $alignedPath }
    if ($null -eq $originalJavaHome) { Remove-Item Env:\JAVA_HOME -ErrorAction SilentlyContinue } else { $env:JAVA_HOME = $originalJavaHome }
}
