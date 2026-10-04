"""Identify only this installation's Windows ADB listener before restarting it."""
import os
import subprocess


def owned_server(adb_path, terminate=False, port=5037):
    if os.name != 'nt':
        return False
    script = r'''
$ErrorActionPreference = 'Stop'
$port = [int]$env:WEBMEMU_ADB_PORT
$owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($owners.Count -ne 1) { exit 2 }
$target = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
if ($target.ExecutablePath -ne $env:WEBMEMU_ADB_PATH -or $target.CommandLine -notmatch 'fork-server server' -or $target.CommandLine -notmatch "(?<![0-9])$port(?![0-9])") { exit 3 }
if ($env:WEBMEMU_ADB_TERMINATE -eq '1') { Stop-Process -Id $target.ProcessId -ErrorAction Stop }
Write-Output 'OWNED'
'''
    env = dict(os.environ, WEBMEMU_ADB_PATH=os.path.abspath(adb_path),
               WEBMEMU_ADB_TERMINATE='1' if terminate else '0', WEBMEMU_ADB_PORT=str(port))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=6, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0 and result.stdout.strip() == 'OWNED'
