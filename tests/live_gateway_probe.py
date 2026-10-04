"""Probe local MEmu host aliases with a temporary echo listener."""
import json
import socket
import subprocess
import threading

listener = socket.socket()
listener.bind(('127.0.0.1',0))
listener.listen(8)
port = listener.getsockname()[1]
def serve():
    while True:
        try:
            client,_ = listener.accept()
            with client:
                client.settimeout(2)
                client.sendall(client.recv(1024))
        except OSError:
            return
threading.Thread(target=serve,daemon=True).start()
report = []
try:
    for address in ('192.168.232.1','192.168.232.3','10.0.2.2','192.168.144.1'):
        p = subprocess.run([r'C:\Users\user\Desktop\ssl_pining\tools\platform-tools\adb.exe',
            '-P','5038','-s','127.0.0.1:21503','shell',f'echo WEBMEMU_GATEWAY | toybox nc -w 1 {address} {port}'],
            capture_output=True,text=True,timeout=4)
        report.append(dict(address=address,code=p.returncode,out=p.stdout,err=p.stderr))
finally:
    listener.close()
print(json.dumps(report))
