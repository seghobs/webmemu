"""Opt-in local proxy echo test through direct adbd reverse channels."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adb_reverse import AdbReverseTunnels

if len(sys.argv) not in (2,3) or sys.argv[1] != '--run':
    raise SystemExit('Use --run [panel URL]')
base = sys.argv[2].rstrip('/') if len(sys.argv) == 3 else None
def panel_command(command):
    req = urllib.request.Request(base+'/api/run_adb', data=json.dumps({'command':command}).encode(),
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as response:
        accepted = json.load(response)
    deadline = time.monotonic()+12
    while time.monotonic() < deadline:
        with urllib.request.urlopen(base+'/api/jobs/'+accepted['job_id'],timeout=5) as response:
            job = json.load(response)
        if job['state'] in ('completed','failed'):
            assert job['state'] == 'completed', job
            return
        time.sleep(.05)
    raise AssertionError('Reverse job deadline exceeded')
listener = socket.socket()
listener.bind(('127.0.0.1',0))
listener.listen(8)
port = listener.getsockname()[1]
def serve():
    while True:
        try:
            client,_ = listener.accept()
        except OSError:
            return
        def echo(sock):
            try:
                with sock:
                    sock.settimeout(3)
                    sock.sendall(sock.recv(4096))
            except OSError:
                pass
        threading.Thread(target=echo,args=(client,),daemon=True).start()
threading.Thread(target=serve,daemon=True).start()
tunnels = AdbReverseTunnels()
report = []
try:
    if base:
        panel_command(f'reverse tcp:{port} tcp:{port}')
    else:
        tunnels.ensure('127.0.0.1:21503',port,port)
    for cycle in range(5):
        result = subprocess.run([r'C:\Users\user\Desktop\ssl_pining\tools\platform-tools\adb.exe',
            '-P','5038','-s','127.0.0.1:21503','shell',f'(echo WEBMEMU_REVERSE_{cycle}; sleep 1) | toybox nc -w 2 127.0.0.1 {port}'],
            capture_output=True,text=True,timeout=5)
        endpoint=tunnels.endpoints.get(('127.0.0.1:21503',port))
        report.append(dict(cycle=cycle+1,code=result.returncode,out=result.stdout,err=result.stderr,
                           service=endpoint.last_open if endpoint else 'panel reverse',error=endpoint.error if endpoint else None))
        assert result.returncode == 0 and f'WEBMEMU_REVERSE_{cycle}' in result.stdout, report[-1]
finally:
    if base:
        panel_command(f'reverse --remove tcp:{port}')
    tunnels.close()
    listener.close()
    Path('artifacts/performance/'+('panel-direct-reverse.json' if base else 'direct-reverse.json')).write_text(json.dumps(report,indent=2))
print(json.dumps(report))
