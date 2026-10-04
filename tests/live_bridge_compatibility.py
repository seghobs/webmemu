"""Opt-in comparison: isolated MEmu ADB tunnel, no device mutations."""
import json
import subprocess
import sys
import time
from pathlib import Path
from adb_recovery import owned_server

adb = str(Path(__file__).resolve().parents[1] / 'tools/platform-tools/adb.exe')
def command(*args):
    return subprocess.run([adb, '-P', '5040', *args], capture_output=True, text=True, timeout=4)
report = []
try:
    startup = subprocess.run([adb, '-P', '5040', '--one-device', 'WEBMEMU_TCP_ONLY', 'start-server'],
                             capture_output=True, text=True, timeout=8)
    assert startup.returncode == 0, startup.stderr
    assert command('connect', '127.0.0.1:21503').returncode == 0
    assert command('-s', '127.0.0.1:21503', 'forward', 'tcp:27044', 'tcp:27042').returncode == 0
    for i in range(5):
        started = time.monotonic()
        probe = subprocess.run([sys.executable, '-c',
            'import frida; print(len(frida.get_device_manager().add_remote_device("127.0.0.1:27044").enumerate_processes()))'],
            capture_output=True, text=True, timeout=8)
        assert probe.returncode == 0, probe.stderr
        time.sleep(3)
        devices = command('devices')
        assert devices.returncode == 0, devices.stderr
        report.append(dict(cycle=i+1, processes=probe.stdout.strip(), ms=round((time.monotonic()-started)*1000,2)))
finally:
    Path('artifacts/performance/bridge-compatibility.json').write_text(json.dumps(report,indent=2))
    owned_server(adb, terminate=True, port=5040)
print(json.dumps(report))
