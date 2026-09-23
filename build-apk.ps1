$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root 'android-tools'
$env:JAVA_HOME = (Resolve-Path (Join-Path $tools 'jdk-17.0.20.1+1')).Path
$env:ANDROID_HOME = (Resolve-Path (Join-Path $tools 'sdk')).Path
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$gradleDir = Join-Path $tools 'gradle-8.10.2'
if (-not (Test-Path (Join-Path $gradleDir 'bin\gradle.bat'))) {
    $zip = Join-Path $tools 'gradle.zip'
    Invoke-WebRequest -Uri 'https://services.gradle.org/distributions/gradle-8.10.2-bin.zip' -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tools -Force
    Remove-Item $zip
}
Push-Location (Join-Path $root 'android')
try {
    & (Join-Path $gradleDir 'bin\gradle.bat') clean assembleDebug --no-daemon
} finally { Pop-Location }
$apk = Join-Path $root 'android\app\build\outputs\apk\debug\app-debug.apk'
if (Test-Path $apk) {
    $out = Join-Path $root 'NEON-SNAKE-debug.apk'
    Copy-Item $apk $out -Force
    Write-Host "APK создан: $out"
}
