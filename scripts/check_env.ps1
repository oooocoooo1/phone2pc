# Check Environment for Phone2PC Build

function Check-Command ($cmd, $name) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "[$([char]0x2713)] $name found." -ForegroundColor Green
        return $true
    } else {
        Write-Host "[$([char]0x2717)] $name NOT found." -ForegroundColor Red
        return $false
    }
}

function Check-Env ($var, $name) {
    if (Test-Path Env:\$var) {
         Write-Host "[$([char]0x2713)] $name set to: $((Get-Item Env:\$var).Value)" -ForegroundColor Green
         return $true
    } else {
        Write-Host "[$([char]0x2717)] $name NOT set." -ForegroundColor Red
        return $false
    }
}

Write-Host "`n=== Phone2PC Build Environment Check ===`n"

$hasGit = Check-Command "git" "Git"
$hasJava = Check-Command "java" "Java (JDK)"
$hasFlutter = Check-Command "flutter" "Flutter SDK"
$hasAndroidHome = Check-Env "ANDROID_HOME" "ANDROID_HOME"

Write-Host "`n=== Recommendations ===`n"

if (-not $hasGit) {
    Write-Host "1. Install Git: https://git-scm.com/download/win"
}
if (-not $hasJava) {
    Write-Host "2. Install JDK 17+: https://adoptium.net/"
}
if (-not $hasFlutter) {
    Write-Host "3. Install Flutter SDK: https://docs.flutter.dev/get-started/install/windows"
    Write-Host "   (Unzip to e.g., C:\src\flutter and add 'bin' to PATH)"
}
if (-not $hasAndroidHome) {
    Write-Host "4. Install Android Studio: https://developer.android.com/studio"
    Write-Host "   (Ensure 'Android SDK Command-line Tools' is selected in SDK Manager)"
}

if ($hasGit -and $hasJava -and $hasFlutter -and $hasAndroidHome) {
    Write-Host "`nEnvironment looks good! You can try building:" -ForegroundColor Cyan
    Write-Host "cd android_app"
    Write-Host "flutter pub get"
    Write-Host "flutter build apk"
} else {
    Write-Host "`nPlease resolve missing dependencies to build the Android App." -ForegroundColor Yellow
}

Write-Host "`nPress any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
