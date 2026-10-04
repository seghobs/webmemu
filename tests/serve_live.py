"""Run the current project on a separate loopback port for live verification."""
import argparse
import os
from pathlib import Path
import sys
import threading

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--frida-port', type=int, required=True)
args = parser.parse_args()
os.environ['WEBMEMU_FRIDA_PORT'] = str(args.frida_port)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
stop = threading.Event()
threading.Thread(target=web.manager.watch_connection, args=(stop,), daemon=True).start()
try:
    web.app.run(host='127.0.0.1', port=args.port, debug=False, threaded=True, use_reloader=False)
finally:
    stop.set()
    web.manager._adb_service.close()
    web.manager._tunnel_service.close()
    web.manager._frida_tunnel.close()
    web.manager._proxy_tunnels.close()
