$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskFrontend = Join-Path $taskRoot 'frontend'
$taskPython = (Get-Command python -ErrorAction Stop).Source
$taskNode = (Get-Command node -ErrorAction Stop).Source
$taskNpm = (Get-Command npm.cmd -ErrorAction Stop).Source
$taskLog = Join-Path $taskRoot 'backend/data'
New-Item -ItemType Directory -Force -Path $taskLog | Out-Null

foreach ($taskPort in @(3001,8010)) {
    $taskListener = Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue
    if ($taskListener) {
        if ($taskPort -eq 3001) {
            try {
                $taskPage = Invoke-WebRequest 'http://127.0.0.1:3001/' -UseBasicParsing -TimeoutSec 3
                $taskHealth = Invoke-RestMethod 'http://127.0.0.1:8010/api/health' -TimeoutSec 3
                if ($taskPage.Content -match 'ACCUNIQ Studio' -and $taskHealth.status -eq 'ok') {
                    Start-Process 'http://127.0.0.1:3001/'
                    Write-Host 'ACCUNIQ Studio zaten calisiyor.'
                    exit 0
                }
            } catch {}
        }
        throw "$taskPort portu kullaniliyor. Mevcut programi kapatin veya belgelerdeki farkli port kurulumunu kullanin."
    }
}
& $taskPython -c 'import fastapi, uvicorn, serial' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -r (Join-Path $taskRoot 'backend/requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python bagimliliklari kurulamadi.' }
}
Push-Location $taskFrontend
try {
    if (-not (Test-Path 'node_modules/next')) {
        & $taskNpm ci --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw 'Frontend bagimliliklari kurulamadi.' }
    }
    $taskBuild = Get-Item '.next/BUILD_ID' -ErrorAction SilentlyContinue
    $taskSources = Get-ChildItem 'app' -Recurse -File
    $taskSources += Get-Item 'package.json','package-lock.json','next.config.ts'
    $taskStale = -not $taskBuild -or ($taskSources | Where-Object { $_.LastWriteTimeUtc -gt $taskBuild.LastWriteTimeUtc })
    if ($taskStale) {
        & $taskNpm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend derlenemedi.' }
    }
} finally { Pop-Location }
$taskBackend = Start-Process -FilePath $taskPython -ArgumentList '-B -m uvicorn backend.main:app --host 127.0.0.1 --port 8010' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLog 'backend-out.log') -RedirectStandardError (Join-Path $taskLog 'backend-error.log') -PassThru
$taskNext = Join-Path $taskFrontend 'node_modules/next/dist/bin/next'
$taskFrontendProcess = Start-Process -FilePath $taskNode -ArgumentList @('"'+$taskNext+'"','start','--hostname','127.0.0.1','--port','3001') -WorkingDirectory $taskFrontend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLog 'frontend-out.log') -RedirectStandardError (Join-Path $taskLog 'frontend-error.log') -PassThru
@{ backend = $taskBackend.Id; frontend = $taskFrontendProcess.Id; started_at = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content (Join-Path $taskLog 'launch-state.json')
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    try {
        $taskHealth = Invoke-RestMethod 'http://127.0.0.1:8010/api/health' -TimeoutSec 2
        $taskPage = Invoke-WebRequest 'http://127.0.0.1:3001/' -UseBasicParsing -TimeoutSec 2
        if ($taskHealth.status -eq 'ok' -and $taskPage.Content -match 'ACCUNIQ Studio') { $taskReady = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 300
}
if (-not $taskReady) { throw "Servisler hazir olmadi. $taskLog dizinindeki hata kayitlarini inceleyin." }
Write-Host 'ACCUNIQ Studio baslatildi: http://127.0.0.1:3001/'
Start-Process 'http://127.0.0.1:3001/'
