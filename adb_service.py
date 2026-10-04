"""Single ADB worker, supervised outside Flask and bounded by external deadlines.

The local supervisor never replays a command. Losing a worker or its reply makes
the outcome uncertain; callers must verify device state before submitting again.
"""
import atexit
import base64
import ctypes
import json
import itertools
import multiprocessing as mp
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time

from adb_recovery import owned_server

MAX_MESSAGE = 8 * 1024 * 1024
HOST_COMMANDS = {'devices', 'connect', 'disconnect', 'start-server', 'kill-server', 'version'}


def _read_message(sock):
    data = bytearray()
    while b'\n' not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise EOFError('ADB hizmeti bağlantıyı kapattı.')
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise ValueError('ADB yanıtı boyut sınırını aştı.')
    return json.loads(data.split(b'\n', 1)[0])


def _write_message(sock, data):
    sock.sendall(json.dumps(data).encode('utf-8') + b'\n')


class _ChildHandle:
    """Keep a handle to the exact child, avoiding PID-reuse kills on Windows."""
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.kernel = kernel
            self.handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)

    def kill(self):
        if os.name == 'nt':
            if self.handle:
                self.kernel.TerminateProcess(self.handle, 1)
        else:
            try:
                os.kill(self.pid, 9)
            except ProcessLookupError:
                pass

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _worker(pipe, server_port=5037):
    while True:
        try:
            task = pipe.recv()
        except EOFError:
            return
        proc = None
        try:
            # No host shell, stdin, or user-supplied process options.
            env = dict(os.environ, ANDROID_ADB_SERVER_PORT=str(server_port))
            env.pop('ADB_SERVER_SOCKET', None)
            proc = subprocess.Popen(task['argv'], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                    env=env)
            pipe.send({'started': proc.pid})
            out, err = proc.communicate(timeout=task['timeout'])
            pipe.send({'returncode': proc.returncode,
                       'stdout': base64.b64encode(out).decode('ascii'),
                       'stderr': base64.b64encode(err).decode('ascii')})
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
            # Do not wait on inherited pipes. The external supervisor also holds
            # the child handle and can replace this whole worker if cleanup hangs.
            pipe.send({'error': 'timeout'})
            return
        except Exception as exc:
            pipe.send({'error': 'execution', 'message': str(exc)})


class AdbService:
    def __init__(self, adb_path, server_port=5038):
        self.adb_path = os.path.abspath(adb_path)
        self.server_port = server_port
        self._process = None
        self._address = None
        self._token = secrets.token_hex(32)
        self._start_lock = threading.Lock()
        self._context = {}
        self._closed = False
        atexit.register(self.close)

    def context(self, **values):
        self._context = dict(values)

    def _start(self):
        if not self._start_lock.acquire(timeout=8):
            raise RuntimeError('ADB hizmeti başlatma süresi aşıldı.')
        try:
            if self._closed:
                raise RuntimeError('ADB hizmeti kapalı.')
            if self._process and self._process.poll() is None:
                return
            if self._process and self._process.stdout:
                self._process.stdout.close()
            env = dict(os.environ, WEBMEMU_BROKER_TOKEN=self._token,
                       WEBMEMU_ADB_SERVER_PORT=str(self.server_port))
            proc = subprocess.Popen([sys.executable, '-B', os.path.abspath(__file__),
                                     '--supervisor', self.adb_path, str(os.getpid())],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            lines = queue.Queue(maxsize=1)
            threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
            try:
                self._address = ('127.0.0.1', json.loads(lines.get(timeout=8))['port'])
            except Exception:
                proc.kill()
                proc.wait(timeout=2)
                raise RuntimeError('Bağımsız ADB gözeticisi başlatılamadı.')
            self._process = proc
        finally:
            self._start_lock.release()

    def _request(self, message, timeout):
        self._start()
        try:
            with socket.create_connection(self._address, timeout=timeout) as sock:
                sock.settimeout(timeout)
                _write_message(sock, dict(message, token=self._token, context=self._context))
                return _read_message(sock)
        except (OSError, EOFError) as exc:
            # The supervisor may have died. Do not replay an uncertain operation.
            raise RuntimeError(f'ADB hizmeti yanıt vermedi ({type(exc).__name__}: {exc}); işlem otomatik tekrarlanmadı.') from exc

    def run(self, argv, timeout=6, text=True, encoding='utf-8', errors='replace', interactive=False, **_):
        args = [str(a) for a in argv]
        if os.path.normcase(os.path.abspath(args[0])) != os.path.normcase(self.adb_path):
            raise ValueError('ADB işçisi yalnızca yapılandırılmış programı çalıştırabilir.')
        result = self._request({'op': 'run', 'argv': args, 'timeout': float(timeout),
                                'interactive': bool(interactive)}, timeout + 12)
        if result.get('error') == 'timeout':
            raise subprocess.TimeoutExpired(args, timeout)
        if result.get('error'):
            raise RuntimeError(result.get('message', 'ADB işçisi kullanılamıyor.'))
        out, err = (base64.b64decode(result.get(k, '')) for k in ('stdout', 'stderr'))
        if text:
            out, err = out.decode(encoding, errors), err.decode(encoding, errors)
        return subprocess.CompletedProcess(args, result['returncode'], out, err)

    def status(self):
        return self._request({'op': 'status'}, 3)

    def recover(self):
        return self._request({'op': 'recover'}, 3).get('accepted', False)

    def close(self):
        proc = self._process
        if self._closed:
            return
        self._closed = True
        if proc and proc.poll() is None:
            try:
                with socket.create_connection(self._address, timeout=1) as sock:
                    _write_message(sock, {'token': self._token, 'op': 'close'})
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=2)
        if proc and proc.stdout:
            proc.stdout.close()


def _parent_alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_uint32()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Supervisor:
    def __init__(self, adb_path, parent_pid, token, server_port=5037):
        self.adb_path, self.parent_pid, self.token = adb_path, parent_pid, token
        self.server_port = server_port
        self.queue = queue.PriorityQueue(maxsize=16)
        # Windows clock resolution can give concurrent requests identical times.
        # A unique sequence prevents heapq from comparing request dictionaries.
        self.sequence = itertools.count()
        self.stop = threading.Event()
        self.context = {}
        self.lock = threading.Lock()
        self.worker = self.pipe = self.child = None
        self.state = {'adb_path': adb_path, 'server_port': server_port, 'worker_restarts': 0, 'server_restarts': 0, 'last_error': None,
                      'recovering': False, 'last_checked': None, 'device_ready': False}
        self.failures = 0
        self.last_recovery = -120
        self.last_host_recovery = -120
        self.request_recovery = threading.Event()
        self.observed_devices = []
        self.state['recent_commands'] = []

    def _replace_worker(self):
        if self.child:
            self.child.kill()
            self.child.close()
            self.child = None
        if self.worker:
            self.worker.terminate()
            self.worker.join(.5)
            if self.worker.is_alive():
                self.worker.kill()
                self.worker.join(.5)
            self.pipe.close()
            self.state['worker_restarts'] += 1
        ctx = mp.get_context('spawn')
        self.pipe, other = ctx.Pipe()
        self.worker = ctx.Process(target=_worker, args=(other, self.server_port), daemon=True)
        self.worker.start()
        other.close()

    def _execute(self, task):
        begin = time.monotonic()
        argv = task['argv'][1:]
        if argv[:1] == ['-s']:
            argv = argv[2:]
        kind = argv[0] if argv else 'unknown'
        if kind == 'shell' and len(argv) > 1:
            kind += ' ' + argv[1].split(' ', 1)[0]
        self.state['current_command'] = kind
        if not self.worker or not self.worker.is_alive():
            self._replace_worker()
        deadline = time.monotonic() + task['timeout'] + .75
        pipe = self.pipe
        sending_failed = threading.Event()
        def send():
            try:
                pipe.send(task)
            except (BrokenPipeError, EOFError, OSError):
                sending_failed.set()
        # Pipe.send itself can block if the worker is suspended and its buffer
        # fills. Keep that wait outside the supervisor's deadline loop as well.
        threading.Thread(target=send, daemon=True).start()
        parent_check = time.monotonic() + 1
        try:
            while time.monotonic() < deadline and not self.stop.is_set():
                if time.monotonic() >= parent_check:
                    if not _parent_alive(self.parent_pid):
                        self.stop.set()
                        break
                    parent_check = time.monotonic() + 1
                if sending_failed.is_set():
                    break
                if not self.pipe.poll(.05):
                    if not self.worker.is_alive():
                        break
                    continue
                result = self.pipe.recv()
                if 'started' in result:
                    self.child = _ChildHandle(result['started'])
                    continue
                if result.get('error') == 'timeout':
                    self._replace_worker()
                elif self.child:
                    self.child.close()
                    self.child = None
                self._record_command(kind, begin, result)
                return result
        except (EOFError, BrokenPipeError, OSError):
            pass
        self._replace_worker()
        self._record_command(kind, begin, {'error': 'timeout'})
        return {'error': 'timeout'}

    def _record_command(self, kind, begin, result):
        self.state['current_command'] = None
        self.state['recent_commands'] = (self.state['recent_commands'] + [dict(
            command=kind, ms=round((time.monotonic()-begin)*1000,2),
            error=result.get('error'), returncode=result.get('returncode'))])[-20:]

    def _command(self, args, timeout=2, serial=None):
        argv = [self.adb_path, *(['-s', serial] if serial else []), *args]
        return self._execute({'argv': argv, 'timeout': timeout})

    def _healthy(self, result):
        return result.get('returncode') == 0 and not result.get('error')

    def _recover(self, host_hung):
        with self.lock:
            context = dict(self.context)
        if context.get('active') or len(set(context.get('devices', [])) | set(self.observed_devices)) > 1:
            self.state['last_error'] = 'Aktif oturum veya birden fazla cihaz: servis sıfırlanmadı.'
            return
        previous = self.last_host_recovery if host_hung else self.last_recovery
        if time.monotonic() - previous < (120 if host_hung else 60):
            return
        if host_hung:
            self.last_host_recovery = time.monotonic()
        else:
            self.last_recovery = time.monotonic()
        self.state['recovering'] = True
        try:
            if host_hung:
                owner_options = {'port': self.server_port} if self.server_port != 5037 else {}
                if not owned_server(self.adb_path, **owner_options):
                    self.state['last_error'] = 'ADB servisinin sahipliği doğrulanamadı.'
                    return
                stopped = self._command(['kill-server'])
                if not self._healthy(stopped):
                    if not owned_server(self.adb_path, terminate=True, **owner_options):
                        return
                if not self._healthy(self._command(['start-server'], timeout=5)):
                    return
                self.state['server_restarts'] += 1
            serial = context.get('serial')
            if serial and serial.startswith('127.0.0.1:'):
                if not host_hung:
                    self._command(['disconnect', serial])
                if not self._healthy(self._command(['connect', serial], timeout=3)):
                    return
            for dev, direction, source, dest in context.get('tunnels', []):
                if direction not in ('reverse', 'forward'):
                    continue
                if not self._healthy(self._command([direction, source, dest], serial=dev)):
                    return
            check = self._command(['shell', 'echo WEBMEMU_READY'], serial=serial) if serial else self._command(['devices'])
            verified = self._healthy(check) and (not serial or base64.b64decode(check.get('stdout', '')).strip() == b'WEBMEMU_READY')
            if verified:
                self.state.update(last_error=None, device_ready=bool(serial), last_checked=time.time())
                self.failures = 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.state['last_error'] = str(exc)
        finally:
            self.state['recovering'] = False

    def _health(self):
        host = self._command(['devices'], timeout=5)
        self.state['last_checked'] = time.time()
        if not self._healthy(host):
            self.failures += 1
            self.state.update(last_error='ADB servisi yanıt vermedi.', device_ready=False)
            if self.failures >= 2:
                self._recover(host_hung=True)
            return
        serial = self.context.get('serial')
        # Remember devices observed by the supervisor too, so a reset is refused
        # when another device appeared since Flask's last successful snapshot.
        observed = [line.split()[0] for line in base64.b64decode(host.get('stdout', '')).decode('utf-8', 'replace').splitlines()
                    if len(line.split()) == 2 and line.split()[1] == 'device']
        with self.lock:
            self.observed_devices = observed
        if serial:
            probe = self._command(['shell', 'echo WEBMEMU_READY'], serial=serial, timeout=5)
            if not self._healthy(probe):
                self.failures += 1
                self.state.update(last_error='Cihaz bağlantısı yanıt vermedi.', device_ready=False)
                if self.failures >= 2:
                    self._recover(host_hung=False)
                return
            if base64.b64decode(probe.get('stdout', '')).strip() != b'WEBMEMU_READY':
                self.state.update(last_error='Cihaz doğrulama yanıtı geçersiz.', device_ready=False)
                return
        self.failures = 0
        self.state.update(last_error=None, device_ready=bool(serial))

    def _handle(self, sock):
        with sock:
            sock.settimeout(3)
            try:
                req = _read_message(sock)
                if not secrets.compare_digest(str(req.get('token', '')), self.token):
                    return
                with self.lock:
                    if 'context' in req:
                        self.context = req['context']
                op = req.get('op')
                if op == 'close':
                    self.stop.set()
                    return
                if op == 'status':
                    _write_message(sock, dict(self.state, supervisor_pid=os.getpid(), worker_pid=self.worker.pid if self.worker else None,
                                              active=bool(self.context.get('active'))))
                    return
                if op == 'recover':
                    self.request_recovery.set()
                    _write_message(sock, {'accepted': True})
                    return
                args = req.get('argv', [])
                timeout = req.get('timeout', 6)
                if (op != 'run' or not args or args[0] != self.adb_path
                        or not isinstance(timeout, (int, float)) or not 0 < timeout <= 180):
                    _write_message(sock, {'error': 'invalid', 'message': 'Geçersiz ADB komutu.'})
                    return
                event, output = threading.Event(), {}
                expiry = time.monotonic() + 6
                read_only = len(args) > 1 and (args[-1].startswith(('ps -A', 'pm list packages', 'settings get', 'logcat -d')) or args[-1] == 'devices')
                priority = 10 if read_only and not req.get('interactive') else 0
                self.queue.put_nowait((priority, next(self.sequence), req, event, output, expiry))
                if not event.wait(timeout + 10):
                    output = {'error': 'busy', 'message': 'ADB kuyruğu bekleme süresi aşıldı; işlem sonucu kontrol edilmeli.'}
                sock.settimeout(2)
                _write_message(sock, output)
            except queue.Full:
                _write_message(sock, {'error': 'busy', 'message': 'ADB kuyruğu dolu; işlem gönderilmedi.'})
            except (OSError, EOFError, ValueError, TypeError) as exc:
                self.state['last_ipc_error'] = f'{type(exc).__name__}: {exc}'
                return

    def serve(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(20)
            listener.settimeout(.25)
            print(json.dumps({'port': listener.getsockname()[1]}), flush=True)
            def accept():
                while not self.stop.is_set():
                    try:
                        sock, _ = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    threading.Thread(target=self._handle, args=(sock,), daemon=True).start()
            threading.Thread(target=accept, daemon=True).start()
            next_health = time.monotonic() + 5
            try:
                while not self.stop.is_set() and _parent_alive(self.parent_pid):
                    if self.request_recovery.is_set() or (self.queue.empty() and time.monotonic() >= next_health):
                        self.request_recovery.clear()
                        self._health()
                        next_health = time.monotonic() + 5
                    try:
                        _, _, req, event, output, expiry = self.queue.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if time.monotonic() >= expiry:
                        output.update(error='busy', message='ADB komutu kuyrukta zaman aşımına uğradı; çalıştırılmadı.')
                    else:
                        output.update(self._execute(req))
                        if output.get('error') == 'timeout':
                            self.state.update(last_error='Takılan ADB işçisi değiştirildi.', device_ready=False)
                            self.request_recovery.set()
                    event.set()
            finally:
                self.stop.set()
                if self.child:
                    self.child.kill()
                    self.child.close()
                if self.worker:
                    self.worker.terminate()
                    self.worker.join(1)
                    if self.worker.is_alive():
                        self.worker.kill()
                        self.worker.join(1)
                if self.pipe:
                    self.pipe.close()


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--supervisor':
        Supervisor(sys.argv[2], int(sys.argv[3]), os.environ['WEBMEMU_BROKER_TOKEN'],
                   int(os.environ.get('WEBMEMU_ADB_SERVER_PORT', '5038'))).serve()
