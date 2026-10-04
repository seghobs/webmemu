"""Opt-in real Frida protocol probes over a direct adbd tunnel."""
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adb_tunnel import AdbTcpTunnel

if sys.argv[1:] != ['--run']:
    raise SystemExit('Use --run for real emulator protocol probes')
tunnel = AdbTcpTunnel()
report = []
try:
    tunnel.ensure('127.0.0.1:21503', 27045, 27042)
    for cycle in range(5):
        started = time.monotonic()
        probe = subprocess.run([sys.executable, '-c',
            'import frida; print(len(frida.get_device_manager().add_remote_device("127.0.0.1:27045").enumerate_processes()))'],
            capture_output=True, text=True, timeout=8)
        assert probe.returncode == 0, (probe.stderr, tunnel.errors)
        report.append(dict(cycle=cycle+1, processes=int(probe.stdout.strip()),
                           ms=round((time.monotonic()-started)*1000,2)))
        time.sleep(2)
finally:
    tunnel.close()
    Path('artifacts/performance/direct-frida.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
