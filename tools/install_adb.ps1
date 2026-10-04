# Keep the panel's ADB separate from the emulator installation.
$ErrorActionPreference = 'Stop'
$taskTools = $PSScriptRoot
$taskZip = Join-Path $taskTools 'platform-tools-latest-windows.zip'
Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $taskZip
Expand-Archive -LiteralPath $taskZip -DestinationPath $taskTools -Force
& (Join-Path $taskTools 'platform-tools\adb.exe') version
if ($LASTEXITCODE -ne 0) { throw 'ADB verification failed' }
