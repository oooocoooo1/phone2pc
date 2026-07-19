param(
    [string]$FlutterRoot = "D:\DevTools\phone2pc\flutter",
    [string]$AndroidSdk = "D:\DevTools\phone2pc\android-sdk",
    [string]$JavaHome = "D:\Program Files\Android\Android Studio\jbr",
    [string]$SigningDirectory = "D:\DevTools\phone2pc\signing"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$androidProject = Join-Path $repositoryRoot "android_app"
$pubspec = Join-Path $androidProject "pubspec.yaml"
$keyStore = Join-Path $SigningDirectory "phone2pc-release.jks"
$secretFile = Join-Path $SigningDirectory "release-password.dpapi"
$recoverySecretFile = Join-Path $SigningDirectory "release-password.recovery"
$unsignedApk = Join-Path $androidProject "build\app\outputs\flutter-apk\app-release.apk"

if (
    -not (Test-Path -LiteralPath $keyStore) -or
    (
        -not (Test-Path -LiteralPath $secretFile) -and
        -not (Test-Path -LiteralPath $recoverySecretFile)
    )
) {
    throw "Release signing credentials are missing from $SigningDirectory"
}

$versionLine = Select-String -LiteralPath $pubspec -Pattern '^version:\s*([^+\s]+)'
if (-not $versionLine) {
    throw "Unable to read the Android version from pubspec.yaml"
}
$version = $versionLine.Matches[0].Groups[1].Value
$outputApk = Join-Path $repositoryRoot "dist\phone2pc-android-release-v$version.apk"

$env:JAVA_HOME = $JavaHome
$env:ANDROID_HOME = $AndroidSdk
$env:ANDROID_SDK_ROOT = $AndroidSdk
$env:PUB_CACHE = "D:\DevTools\phone2pc\pub-cache"
$env:GRADLE_USER_HOME = "D:\DevTools\phone2pc\gradle-home"
$env:FLUTTER_STORAGE_BASE_URL = "https://storage.flutter-io.cn"
$env:PUB_HOSTED_URL = "https://pub.flutter-io.cn"
$env:Path = "$FlutterRoot\bin;$FlutterRoot\bin\mingit\cmd;$JavaHome\bin;$env:Path"

Push-Location $androidProject
try {
    & flutter --no-version-check build apk --release
    if ($LASTEXITCODE -ne 0) { throw "Flutter release build failed" }
} finally {
    Pop-Location
}

try {
    $encrypted = Get-Content -LiteralPath $secretFile -Raw
    $secure = ConvertTo-SecureString $encrypted
    $credential = New-Object System.Management.Automation.PSCredential("phone2pc", $secure)
    $releasePassword = $credential.GetNetworkCredential().Password
} catch {
    $releasePassword = Get-Content -LiteralPath $recoverySecretFile -Raw
}
$env:PHONE2PC_RELEASE_PASS = $releasePassword
$apksigner = Join-Path $AndroidSdk "build-tools\36.0.0\apksigner.bat"

try {
    & $apksigner sign `
        --ks $keyStore `
        --ks-key-alias phone2pc `
        --ks-pass env:PHONE2PC_RELEASE_PASS `
        --key-pass env:PHONE2PC_RELEASE_PASS `
        --min-sdk-version 26 `
        --out $outputApk `
        $unsignedApk
    if ($LASTEXITCODE -ne 0) { throw "APK signing failed" }

    & $apksigner verify --verbose --print-certs $outputApk
    if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed" }
} finally {
    Remove-Item Env:PHONE2PC_RELEASE_PASS -ErrorAction SilentlyContinue
}

Write-Output "Signed release APK: $outputApk"
