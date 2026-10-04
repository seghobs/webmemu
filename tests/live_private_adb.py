"""Read-only comparison of an isolated ADB server against MEmu's shared server."""
import json
import os
from pathlib import Path
import subprocess
import time

adb = r'C:\Program Files\Microvirt\MEmu\adb.exe'
env = dict(os.environ, ANDROID_ADB_SERVER_PORT='5038')
commands = [['devices'], ['-s', '127.0.0.1:21503', 'shell', 'ps -A'],
            ['-s', '127.0.0.1:21503', 'shell', 'pm list packages -3']]
samples = []
for i in range(90):
    start = time.monotonic()
    try:
        p = subprocess.run([adb, *commands[i % 3]], capture_output=True, text=True, timeout=3, env=env)
        samples.append(dict(ms=round((time.monotonic()-start)*1000, 2), code=p.returncode, error=p.stderr, size=len(p.stdout)))
    except subprocess.TimeoutExpired:
        samples.append(dict(timeout=True))
    time.sleep(.2)
Path('artifacts/performance/private-adb-probe.json').write_text(json.dumps(samples, indent=2))
print(json.dumps(dict(commands=len(samples), timeouts=sum(bool(s.get('timeout')) for s in samples),
                     nonzero=sum(bool(s.get('code')) for s in samples), max_ms=max(s.get('ms', 0) for s in samples))))
