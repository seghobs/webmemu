import os
import subprocess
import threading
import shlex
import re
import tempfile
import shutil
import ipaddress
import time
import glob
import socket
import json
from functools import wraps
from runtime import SnapshotCache, JobRegistry
from adb_recovery import owned_server
from adb_service import AdbService
from adb_tunnel import AdbTcpTunnel
from adb_reverse import AdbReverseTunnels
from datetime import datetime
from flask import Flask, jsonify, request, render_template, Response, copy_current_request_context

def run_command(*args, **kwargs):
    kwargs.setdefault('timeout', 15)
    current = globals().get('manager')
    if args and current and current._adb_service:
        command = args[0]
        is_adb = (isinstance(command, (list, tuple)) and command
                  and os.path.normcase(os.path.abspath(command[0])) == os.path.normcase(os.path.abspath(current.adb_path)))
        if isinstance(command, str):
            is_adb = command.startswith(f'"{current.adb_path}" ')
        if is_adb:
            return current.run_external_adb(command, **kwargs)
    return subprocess.run(*args, **kwargs)

app = Flask(__name__)

class BypassManager:
    def __init__(self, command_executor=None):
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.memu_dir = self.find_memu_dir()
        if self.memu_dir:
            self.adb_path = os.path.join(self.memu_dir, "adb.exe")
        else:
            self.adb_path = r"C:\Program Files\Microvirt\MEmu\adb.exe"
        bundled_adb = os.path.join(self.project_dir, 'tools', 'platform-tools', 'adb.exe')
        if command_executor is None and os.path.isfile(bundled_adb):
            self.adb_path = bundled_adb
        self.device_connected = False
        self.frida_server_running = False
        self.bypass_running = False
        self.bypass_process = None
        self.extra_processes = []
        self._log_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._session_id = 0
        self.log_revision = 0
        self.log_history = []
        self.proxy_status = "Bilinmiyor"
        self.stealth_mode = False
        self.stealth_binary_name = "sys_agent"
        self.stealth_port = 13337
        self.frida_local_port = int(os.environ.get('WEBMEMU_FRIDA_PORT', '27043'))
        if not 0 < self.frida_local_port < 65536:
            raise ValueError('Geçersiz Frida yerel portu.')
        self._fail_probe_count = 0
        self._cached_cert_status = ("not_installed", False)
        self._last_cert_check_time = 0
        self.cache = SnapshotCache()
        self.jobs = JobRegistry()
        self.device_serial = None
        self._device_lock = threading.Lock()
        self._adb_lock = threading.RLock()
        self._last_discovery = 0
        self._cert_hash_cache = None
        self._probe_failures = 0
        self._last_recovery = 0
        self._recovery_lock = threading.Lock()
        self._last_server_recovery = -120
        self._last_tunnel_snapshot = -30
        self._saved_tunnels = []
        self._known_devices = []
        self._command_executor = command_executor
        self._adb_service = AdbService(self.adb_path) if command_executor is None else None
        self._tunnel_service = AdbService(self.adb_path, server_port=5039) if command_executor is None else None
        self._tunnel_serial = None
        self._tunnel_lock = threading.Lock()
        self._adb_supervisor_state = {}
        self._tunnel_supervisor_state = {}
        self._frida_tunnel = AdbTcpTunnel() if command_executor is None else None
        self._proxy_tunnels = AdbReverseTunnels() if command_executor is None else None

    def run_external_adb(self, command, **kwargs):
        if isinstance(command, str):
            tail = command[len(f'"{self.adb_path}" '):].strip()
            if tail.startswith('shell '):
                remote = tail[6:].strip()
                if remote.startswith('"') and remote.endswith('"'):
                    remote = remote[1:-1]
                args = ['shell', remote]
            else:
                args = [x.strip('"') for x in shlex.split(tail, posix=False)]
        else:
            args = list(command[1:])
        if self.device_serial and args and args[0] not in ('devices', 'connect', 'disconnect', 'start-server', 'kill-server', 'version', '-s'):
            args = ['-s', self.device_serial, *args]
        serial = self.device_serial or (self._known_devices[0] if len(self._known_devices) == 1 else None)
        self._adb_service.context(serial=serial,
                                  devices=self._known_devices, tunnels=[],
                                  active=self.bypass_running or self.jobs.running())
        actual = args[2:] if args[:1] == ['-s'] else args
        if self._proxy_tunnels and actual[:1] == ['reverse']:
            empty = '' if kwargs.get('text', bool(kwargs.get('encoding'))) else b''
            if len(actual) == 3 and all(re.fullmatch(r'tcp:[0-9]{1,5}', p) for p in actual[1:]):
                self._proxy_tunnels.ensure(serial, int(actual[1][4:]), int(actual[2][4:]))
                return subprocess.CompletedProcess([self.adb_path, *args], 0, empty, empty)
            if len(actual) == 3 and actual[1] == '--remove' and re.fullmatch(r'tcp:[0-9]{1,5}', actual[2]):
                self._proxy_tunnels.remove(serial, int(actual[2][4:]))
                return subprocess.CompletedProcess([self.adb_path, *args], 0, empty, empty)
            if actual[1:] == ['--list']:
                rows = self._proxy_tunnels.rows(serial)
                output = ''.join(f'{device} tcp:{source} tcp:{target}\n' for device, source, target in rows)
                return subprocess.CompletedProcess([self.adb_path, *args], 0,
                                                   output if isinstance(empty,str) else output.encode(), empty)
        if self._frida_tunnel and actual == ['forward', '--list']:
            # Our owned mappings are authoritative. A periodic status read must
            # not launch the legacy Windows data server or wait on its sockets.
            with self._frida_tunnel.lock:
                rows = [(f'127.0.0.1:{target[1]}', f'tcp:{port}', f'tcp:{target[2]}')
                        for port, (_, target, enabled) in self._frida_tunnel.listeners.items() if enabled]
            rows.extend((device, source, target) for device, direction, source, target in self._saved_tunnels
                        if direction == 'forward')
            output = ''.join(f'{device} {source} {target}\n' for device, source, target in sorted(set(rows)))
            is_text = kwargs.get('text', bool(kwargs.get('encoding')))
            return subprocess.CompletedProcess([self.adb_path, *args], 0,
                                               output if is_text else output.encode(), '' if is_text else b'')
        if self._frida_tunnel and actual[:1] == ['forward'] and len(actual) == 3:
            frida_ports = {f'tcp:{self.frida_local_port}': 27042, f'tcp:{self.stealth_port}': self.stealth_port}
            if actual[1] in frida_ports and actual[2] == f'tcp:{frida_ports[actual[1]]}':
                self._frida_tunnel.ensure(serial, int(actual[1][4:]), frida_ports[actual[1]])
                empty = '' if kwargs.get('text', bool(kwargs.get('encoding'))) else b''
                return subprocess.CompletedProcess([self.adb_path, *args], 0, empty, empty)
            if actual[1] == '--remove' and actual[2] in frida_ports:
                self._frida_tunnel.remove(int(actual[2][4:]))
                empty = '' if kwargs.get('text', bool(kwargs.get('encoding'))) else b''
                return subprocess.CompletedProcess([self.adb_path, *args], 0, empty, empty)
        service = self._adb_service
        if self._tunnel_service and actual and actual[0] in ('forward', 'reverse'):
            service = self._tunnel_service
            service.context(serial=serial, devices=self._known_devices, tunnels=self._saved_tunnels,
                            active=self.bypass_running)
            if serial and serial != self._tunnel_serial:
                if not self._tunnel_lock.acquire(timeout=5):
                    raise RuntimeError('Trafik tüneli bağlantısı hazırlanıyor.')
                try:
                    if serial != self._tunnel_serial:
                        connected = service.run([self.adb_path, 'connect', serial], timeout=3, text=True)
                        if connected.returncode != 0 or 'connected to' not in connected.stdout:
                            raise RuntimeError('Trafik tüneli cihaza bağlanamadı.')
                        self._tunnel_serial = serial
                finally:
                    self._tunnel_lock.release()
        kwargs['text'] = kwargs.get('text', bool(kwargs.get('encoding')))
        kwargs['interactive'] = threading.current_thread().name == 'device-actions'
        result = service.run([self.adb_path, *args], **kwargs)
        # Remember tunnel changes immediately, including removals between probes.
        serial = self.device_serial
        actual = args[2:] if args[:1] == ['-s'] else args
        if result.returncode == 0 and actual and actual[0] in ('reverse', 'forward'):
            direction = actual[0]
            if len(actual) == 3 and not actual[1].startswith('--'):
                self._saved_tunnels = [t for t in self._saved_tunnels if t[:3] != (serial, direction, actual[1])]
                self._saved_tunnels.append((serial, direction, actual[1], actual[2]))
            elif len(actual) == 3 and actual[1] == '--remove':
                self._saved_tunnels = [t for t in self._saved_tunnels if t[:3] != (serial, direction, actual[2])]
        return result

    def script_path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.js", name, re.IGNORECASE):
            raise ValueError("Geçerli bir .js dosya adı gereklidir.")
        path = os.path.join(self.project_dir, name)
        if os.path.islink(path) or os.path.dirname(os.path.realpath(path)) != os.path.realpath(self.project_dir):
            raise ValueError("Script proje dizininde olmalıdır.")
        return path

    def find_memu_dir(self):
        # 1. Try registry
        try:
            import winreg
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MEmu"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microvirt\MEmu"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microvirt\MEmu"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microvirt\MEmu")
            ]
            for hkey, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        for val_name in ["InstallLocation", "InstallPath", "Path", "UninstallString"]:
                            try:
                                path, _ = winreg.QueryValueEx(key, val_name)
                                if path:
                                    if "uninstall" in val_name.lower() or "uninst" in val_name.lower():
                                        path = os.path.dirname(path)
                                    path = path.strip('"')
                                    if os.path.exists(path):
                                        if os.path.exists(os.path.join(path, "MEmu.exe")):
                                            return path
                                        parent = os.path.dirname(path)
                                        if os.path.exists(os.path.join(parent, "MEmu.exe")):
                                            return parent
                            except FileNotFoundError:
                                continue
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Check common paths on all drives
        common_subs = [
            r"Program Files\Microvirt\MEmu",
            r"Program Files (x86)\Microvirt\MEmu",
            r"Microvirt\MEmu",
            r"MEmu"
        ]

        # Get drives
        try:
            import string
            from ctypes import windll
            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(letter + ":\\")
                bitmask >>= 1
        except Exception:
            drives = ["C:\\", "D:\\", "E:\\", "F:\\"]

        for drive in drives:
            for sub in common_subs:
                check_path = os.path.join(drive, sub)
                if os.path.exists(os.path.join(check_path, "MEmu.exe")):
                    return check_path

        # 3. Last resort search (shallow scan)
        for drive in drives:
            if not os.path.exists(drive):
                continue
            for root_dir in [drive, os.path.join(drive, "Program Files"), os.path.join(drive, "Program Files (x86)")]:
                if not os.path.exists(root_dir):
                    continue
                try:
                    for entry in os.scandir(root_dir):
                        if entry.is_dir() and entry.name.lower() in ["microvirt", "memu"]:
                            if entry.name.lower() == "memu" and os.path.exists(os.path.join(entry.path, "MEmu.exe")):
                                return entry.path
                            for sub_entry in os.scandir(entry.path):
                                if sub_entry.is_dir() and sub_entry.name.lower() == "memu":
                                    if os.path.exists(os.path.join(sub_entry.path, "MEmu.exe")):
                                        return sub_entry.path
                except Exception:
                    pass
        return None

    def start_memu(self):
        if not hasattr(self, 'memu_dir') or not self.memu_dir:
            self.memu_dir = self.find_memu_dir()
            if self.memu_dir:
                if not self._adb_service:
                    self.adb_path = os.path.join(self.memu_dir, "adb.exe")

        if not self.memu_dir or not os.path.exists(os.path.join(self.memu_dir, "MEmu.exe")):
            self.log("MEmu Player kurulum dizini bulunamadı!", "error")
            return False, "MEmu Player kurulum dizini bulunamadı!"

        memu_exe = os.path.join(self.memu_dir, "MEmu.exe")
        self.log(f"MEmu Player başlatılıyor: {memu_exe}", "process")
        try:
            subprocess.Popen(f'"{memu_exe}"', shell=True)
            self.log("MEmu Player başlatma komutu gönderildi. Cihazın açılması biraz zaman alabilir.", "success")

            # Start automatic status checks
            def delayed_checks():
                for i in range(12): # check every 5 seconds for 1 minute
                    time.sleep(5)
                    self.log("Cihaz bağlantısı kontrol ediliyor...", "info")
                    if self.check_devices():
                        self.log("MEmu Player başarıyla bağlandı ve hazır hale geldi!", "success")
                        break
            threading.Thread(target=delayed_checks, daemon=True).start()

            return True, "MEmu Player başlatılıyor..."
        except Exception as e:
            self.log(f"MEmu başlatılamadı: {e}", "error")
            return False, str(e)

    def start_mitmweb(self):
        self.log("Mitmweb yeni bir CMD penceresinde başlatılıyor...", "process")
        try:
            subprocess.Popen("start cmd.exe /k \"mitmweb --set web_password=123456 --no-web-open-browser\"", shell=True)
            self.log("Mitmweb başarıyla başlatıldı!", "success")

            def open_browser():
                import webbrowser
                time.sleep(2.0)
                webbrowser.open("http://127.0.0.1:8081/?token=123456")
                self.log("Mitmweb arayüzü tarayıcıda otomatik olarak açıldı.", "success")

            threading.Thread(target=open_browser, daemon=True).start()
            return True, "Mitmweb başlatıldı!"
        except Exception as e:
            self.log(f"Mitmweb başlatılamadı: {e}", "error")
            return False, str(e)

    def log(self, message, level="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon_map = {
            "info": "⚪",
            "success": "🟢",
            "error": "🔴",
            "warning": "🟡",
            "process": "🔵",
            "terminal": "💻"
        }
        icon = icon_map.get(level, "info")
        formatted = f"{icon} [{timestamp}] {message}"
        with self._log_lock:
            self.log_history.append(formatted)
            del self.log_history[:-100]
            self.log_revision += 1

    def adb(self, *args, timeout=6):
        command = [self.adb_path]
        if self.device_serial and args and args[0] not in ('devices', 'connect', 'disconnect', 'start-server'):
            command += ['-s', self.device_serial]
        if self._adb_service:
            return self.run_external_adb([*command, *args], timeout=timeout, text=True,
                                         encoding='utf-8', errors='replace')
        with self._adb_lock:
            return self._command_executor([*command, *args], shell=False, capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=timeout)

    def quick_check_device_alive(self):
        return self.check_devices(discover=False, details=False)

    def check_devices(self, discover=True, details=True):
        # Existing connections win. Never scan twenty closed ports on a connected device.
        with self._device_lock:
            try:
                result = self.adb('devices', timeout=3)
                devices = [line.split()[0] for line in result.stdout.splitlines()
                           if len(line.split()) == 2 and line.split()[1] == 'device']
                if result.returncode != 0:
                    raise OSError('ADB cihaz listesi alınamadı.')
                self._known_devices = devices
                if not devices and discover and time.monotonic() - self._last_discovery > 5:
                    self._last_discovery = time.monotonic()
                    ports = [21503 + i * 10 for i in range(16)] + [5555, 62001, 16384]
                    for port in ports:
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=0.08):
                                pass
                        except OSError:
                            continue
                        self.adb('connect', f'127.0.0.1:{port}', timeout=2)
                        result = self.adb('devices', timeout=3)
                        devices = [line.split()[0] for line in result.stdout.splitlines()
                                   if len(line.split()) == 2 and line.split()[1] == 'device']
                        if devices:
                            break
                previous = self.device_connected
                self._known_devices = devices
                self.device_connected = bool(devices)
                if devices:
                    selected = self.device_serial if self.device_serial in devices else devices[0]
                    if selected != self.device_serial:
                        self.cache.invalidate()
                        self._last_cert_check_time = 0
                        self.device_serial = selected
                    if not previous:
                        self.log('Cihaz bağlantısı algılandı.', 'success')
                else:
                    self.device_serial = None
                    self.frida_server_running = False
                    self.proxy_status = 'Cihaz Yok'
                    if previous:
                        self.cache.invalidate()
                        self.log('Cihaz bağlantısı kesildi.', 'warning')
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.device_connected = False
                self.frida_server_running = False
                self.proxy_status = 'Bilinmiyor'
                raise RuntimeError('ADB yanıt vermiyor; bağlantı yeniden denenecek.') from exc
        if self.device_connected and details:
            self.check_frida_status()
            self.check_proxy_status()
        return self.device_connected

    def check_frida_status(self):
        if not self.device_connected:
            self.frida_server_running = False
            return False
        result = self.adb('shell', 'ps -A', timeout=3)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'Frida durumu okunamadı.')
        names = {line.split()[-1].rsplit('/', 1)[-1] for line in result.stdout.splitlines() if line.split()}
        target = self.stealth_binary_name if self.stealth_mode else 'frida-server'
        running = target in names
        if running != self.frida_server_running:
            self.log('Frida server aktif' if running else 'Frida server çalışmıyor', 'success' if running else 'warning')
        self.frida_server_running = running
        return running

    def check_proxy_status(self):
        if not self.device_connected:
            self.proxy_status = 'Cihaz Yok'
            return
        result = self.adb('shell', 'settings get global http_proxy', timeout=3)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'Proxy durumu okunamadı.')
        value = result.stdout.strip()
        self.proxy_status = 'Kapatıldı' if value in ('', 'null', ':0') else value

    def status_snapshot(self):
        cert, installed = self._cached_cert_status if self.device_connected else ('no_device', False)
        return dict(device_connected=self.device_connected, frida_server_running=self.frida_server_running,
                    bypass_running=self.bypass_running, proxy_status=self.proxy_status, cert_status=cert,
                    cert_installed=installed, stealth_mode=self.stealth_mode, stealth_port=self.stealth_port,
                    stealth_binary_name=self.stealth_binary_name, device_serial=self.device_serial,
                    adb_service=dict(self._adb_supervisor_state),
                    tunnel_service=dict(self._tunnel_supervisor_state),
                    frida_transport='direct_adbd' if self._frida_tunnel else 'adb_forward',
                    proxy_transport='direct_adbd' if self._proxy_tunnels else 'adb_reverse',
                    frida_endpoint=f'127.0.0.1:{self.stealth_port if self.stealth_mode else self.frida_local_port}')

    def refresh_status(self):
        try:
            self.check_devices(discover=True, details=True)
            self._cached_cert_status = self.check_cert_status()
            self.remember_tunnels()
            result = self.status_snapshot()
            self._probe_failures = 0
            result.update(checked_at=time.time(), error=None)
            return result
        except Exception as exc:
            result = self.status_snapshot()
            self._probe_failures += 1
            recovered = self._probe_failures >= 2 and self.recover_adb()
            message = 'ADB bağlantısı yenilendi; cihaz kontrol ediliyor.' if recovered else 'Cihaz sorgusu yanıt vermedi. Bağlantı kontrol ediliyor.'
            result.update(checked_at=time.time(), error=message, cert_status='unknown', cert_installed=False)
            return result

    def remember_tunnels(self):
        if not self.device_connected or time.monotonic() - self._last_tunnel_snapshot < 30:
            return
        try:
            reverse = self.adb('reverse', '--list', timeout=2)
            forward = self.adb('forward', '--list', timeout=2)
            if reverse.returncode or forward.returncode:
                return
            saved = []
            for row in reverse.stdout.splitlines():
                parts = row.split()
                if len(parts) == 3:
                    saved.append((self.device_serial, 'reverse', *parts[-2:]))
            for row in forward.stdout.splitlines():
                parts = row.split()
                if len(parts) == 3:
                    saved.append((parts[0], 'forward', *parts[-2:]))
            self._saved_tunnels = saved
            self._last_tunnel_snapshot = time.monotonic()
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            pass

    def recover_adb(self):
        if self.bypass_running or self.jobs.running():
            return False
        if self._adb_service:
            self._adb_service.context(serial=self.device_serial or (self._known_devices[0] if len(self._known_devices) == 1 else None),
                                      devices=self._known_devices, tunnels=[], active=False)
            self._adb_service.recover()
            return False  # An accepted request is not proof of successful recovery.
        try:
            probe = self.adb('devices', timeout=2)
            if probe.returncode == 0:
                return self.recover_transport()
            return False
        except subprocess.TimeoutExpired:
            return self.recover_adb_server()
        except OSError:
            return False

    def recover_adb_server(self):
        if self._adb_service:
            return self.recover_adb()
        # A host reset affects every ADB client. Do not reset another installation,
        # an active session, or a server last observed with multiple devices.
        if (self.bypass_running or self.jobs.running() or len(self._known_devices) > 1
                or time.monotonic() - self._last_server_recovery < 120
                or not self._recovery_lock.acquire(blocking=False)):
            return False
        try:
            with self._adb_lock:
                if self.bypass_running or self.jobs.running():
                    return False
                self._last_server_recovery = time.monotonic()
                if not owned_server(self.adb_path):
                    self.log('ADB servisinin sahipliği doğrulanamadı; otomatik yeniden başlatılmadı.', 'warning')
                    return False
                self.log('ADB servisi yanıt vermiyor; otomatik yeniden başlatılıyor...', 'warning')
                try:
                    stopped = self.adb('kill-server', timeout=2)
                    if stopped.returncode != 0:
                        return False
                except subprocess.TimeoutExpired:
                    if not owned_server(self.adb_path, terminate=True):
                        return False
                if self.adb('start-server', timeout=5).returncode != 0:
                    return False
                serials = set(self._known_devices)
                serials.update(t[0] for t in self._saved_tunnels)
                for serial in serials:
                    if re.fullmatch(r'127\.0\.0\.1:\d+', serial):
                        if self.adb('connect', serial, timeout=3).returncode != 0:
                            return False
                for serial, direction, source, dest in self._saved_tunnels:
                    restored = run_command([self.adb_path, '-s', serial, direction, source, dest],
                                           shell=False, capture_output=True, timeout=3)
                    if restored.returncode != 0:
                        return False
                check = self.adb('devices', timeout=3)
                if check.returncode != 0:
                    return False
                self._last_cert_check_time = 0
                self._probe_failures = 0
                self.cache.invalidate()
                self.log('ADB servisi yeniden başlatıldı; cihaz bağlantısı kontrol ediliyor.', 'success')
                return True
        except (OSError, subprocess.TimeoutExpired):
            self.log('ADB servisi toparlanamadı; yeniden denemeden önce beklenecek.', 'warning')
            return False
        finally:
            self._recovery_lock.release()

    def watch_connection(self, stop):
        while not stop.is_set():
            self.cache.get('status', self.refresh_status, 1.5, self.status_snapshot())
            if self._adb_service:
                try:
                    self._adb_service.context(serial=self.device_serial or (self._known_devices[0] if len(self._known_devices) == 1 else None),
                                              devices=self._known_devices, tunnels=[],
                                              active=self.bypass_running or self.jobs.running())
                    observed = self._adb_service.status()
                    if observed.get('worker_restarts', 0) > self._adb_supervisor_state.get('worker_restarts', 0):
                        self.log('Yanıt vermeyen ADB işçisi yenilendi.', 'warning')
                    if observed.get('server_restarts', 0) > self._adb_supervisor_state.get('server_restarts', 0):
                        self.log('ADB servisi otomatik yeniden başlatıldı; bağlantı kontrol ediliyor.', 'process')
                    self._adb_supervisor_state = observed
                except RuntimeError:
                    pass
            if self._tunnel_service and self._tunnel_service._process is not None:
                try:
                    self._tunnel_service.context(serial=self.device_serial, devices=self._known_devices,
                                                 tunnels=self._saved_tunnels,
                                                 active=self.bypass_running)
                    self._tunnel_supervisor_state = self._tunnel_service.status()
                except RuntimeError:
                    pass
            stop.wait(5)

    def recover_transport(self, for_action=False):
        if self._adb_service:
            if self.bypass_running:
                return False
            self._adb_service.recover()
            return False
        serial = self.device_serial
        if not serial or not re.fullmatch(r'127\.0\.0\.1:\d+', serial) or self.bypass_running or (self.jobs.running() and not for_action):
            return False
        if time.monotonic() - self._last_recovery < 60 or not self._recovery_lock.acquire(blocking=False):
            return False
        self._adb_lock.acquire()
        try:
            self._last_recovery = time.monotonic()
            self.log('Yanıt vermeyen cihazın ADB bağlantısı yenileniyor...', 'warning')
            reverse = self.adb('reverse', '--list', timeout=2)
            forward = self.adb('forward', '--list', timeout=2)
            self.adb('disconnect', serial, timeout=3)
            connected = self.adb('connect', serial, timeout=3)
            if connected.returncode != 0:
                return False
            for row in reverse.stdout.splitlines():
                parts = row.split()
                if len(parts) == 3:
                    self.adb('reverse', *parts[-2:], timeout=2)
            for row in forward.stdout.splitlines():
                parts = row.split()
                if len(parts) == 3 and parts[0] == serial:
                    self.adb('forward', *parts[-2:], timeout=2)
            probe = self.adb('shell', 'echo WEBMEMU_READY', timeout=2)
            ready = probe.returncode == 0 and probe.stdout.strip() == 'WEBMEMU_READY'
            if ready:
                self._last_cert_check_time = 0
                self.cache.invalidate()
            return ready
        except (OSError, subprocess.TimeoutExpired):
            return False
        finally:
            self._adb_lock.release()
            self._recovery_lock.release()

    def install_frida_server(self):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Kurulum için cihaz bağlı olmalıdır!", "error")
            return False

        target_name = self.stealth_binary_name if self.stealth_mode else "frida-server"
        device_dest = f"/data/local/tmp/{target_name}"

        self.log(f"Cihaz CPU mimarisi sorgulanıyor (Hedef: {target_name})...", "process")
        # Get CPU ABI
        abi_res = self.adb('shell', 'getprop ro.product.cpu.abi')
        abi = abi_res.stdout.strip().lower()
        self.log(f"Cihaz mimarisi: {abi}", "info")

        # Map Android ABI to Frida releases architecture names
        arch_map = {
            "x86_64": "x86_64",
            "x86": "x86",
            "arm64-v8a": "arm64",
            "arm64": "arm64",
            "armeabi-v7a": "arm",
            "armeabi": "arm",
            "arm": "arm"
        }

        frida_arch = arch_map.get(abi, "x86_64") # fallback to x86_64
        version = "16.2.2"
        expected_filename = f"frida-server-{version}-android-{frida_arch}"
        local_path = os.path.join(self.project_dir, expected_filename)

        # 1. Check if already installed on device
        check_res = self.adb('shell', f'ls {device_dest}')
        if target_name in check_res.stdout:
            ver_res = self.adb('shell', f'{device_dest} --version')
            current_ver = ver_res.stdout.strip()
            if current_ver == version:
                self.log(f"Cihazda uyumlu Frida Server ({version} / {target_name}) zaten yüklü.", "success")
                return True
            else:
                self.log(f"Cihazdaki Frida Server sürümü farklı ({current_ver}). Güncelleniyor...", "warning")

        # 2. Look for local binary in project folder
        if os.path.exists(local_path):
            self.log(f"Yerel Frida Server dosyası bulundu: {expected_filename}", "info")
        else:
            generic_path = os.path.join(self.project_dir, "frida-server")
            if os.path.exists(generic_path):
                local_path = generic_path
                self.log("Yerel genel 'frida-server' dosyası bulundu.", "info")
            else:
                # 3. Download from GitHub Releases
                download_url = f"https://github.com/frida/frida/releases/download/{version}/frida-server-{version}-android-{frida_arch}.xz"
                xz_path = local_path + ".xz"
                self.log(f"Frida Server {version} indiriliyor: {download_url}", "process")

                try:
                    import urllib.request
                    import lzma

                    with urllib.request.urlopen(download_url, timeout=20) as response, open(xz_path, 'wb') as download:
                        shutil.copyfileobj(response, download)
                    self.log("İndirme tamamlandı, arşiv açılıyor...", "process")

                    with lzma.open(xz_path) as f_in:
                        with open(local_path, 'wb') as f_out:
                            f_out.write(f_in.read())

                    if os.path.exists(xz_path):
                        os.remove(xz_path)

                    self.log("Arşiv başarıyla açıldı ve kaydedildi.", "success")
                except Exception as e:
                    self.log(f"İndirme veya çıkarma hatası: {e}", "error")
                    return False

        # 4. Push binary to device and configure permissions
        self.log(f"Dosya cihaza yükleniyor ({device_dest})...", "process")
        run_command(f'"{self.adb_path}" shell "su -c \'pkill -9 frida; pkill -9 {self.stealth_binary_name}\'"', shell=True, capture_output=True)

        push_res = run_command(f'"{self.adb_path}" push "{local_path}" {device_dest}', shell=True, capture_output=True, text=True)
        if push_res.returncode != 0:
            self.log(f"Dosya yükleme hatası: {push_res.stderr}", "error")
            return False

        self.log("Çalıştırma izinleri tanımlanıyor...", "process")
        run_command(f'"{self.adb_path}" shell "su -c \'chmod 755 {device_dest}\'"', shell=True, capture_output=True)

        self.log(f"Frida Server ({target_name}) başarıyla kuruldu!", "success")
        return True

    def start_frida_server(self):
        if not self.check_devices(details=False):
            return False
        if self.check_frida_status():
            self._ensure_frida_tunnel()
            return True
        if not self.install_frida_server():
            return False
        target = self.stealth_binary_name if self.stealth_mode else 'frida-server'
        port_args = f' -l 0.0.0.0:{self.stealth_port}' if self.stealth_mode else ''
        self.log(f'Frida server başlatılıyor ({target})...', 'process')
        # MEmu's su/ADB can retain the daemon's inherited streams after -D.
        # Detach all three streams, preserving startup errors on the device.
        startup_log = f'/data/local/tmp/{target}-start.log'
        try:
            result = self.adb(
                'shell',
                f"su -c '/data/local/tmp/{target}{port_args} -D </dev/null >{startup_log} 2>&1'",
                timeout=3,
            )
        except subprocess.TimeoutExpired:
            # A transport timeout does not prove that the daemon failed to start.
            # Reconcile with device state before reporting an outcome.
            result = None
        if result is not None and result.returncode != 0:
            self.log(result.stderr.strip() or result.stdout.strip() or 'Frida başlatılamadı.', 'error')
            self._log_frida_start_error(startup_log)
            return False
        if self.stealth_mode:
            result = self.adb('forward', f'tcp:{self.stealth_port}', f'tcp:{self.stealth_port}')
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or 'Port yönlendirmesi başarısız.')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.check_frida_status():
                self._ensure_frida_tunnel()
                return True
            time.sleep(0.15)
        self.log('Frida başlatma doğrulaması başarısız.', 'error')
        self._log_frida_start_error(startup_log)
        return False

    def _ensure_frida_tunnel(self):
        if self._frida_tunnel:
            local_port = self.stealth_port if self.stealth_mode else self.frida_local_port
            remote_port = self.stealth_port if self.stealth_mode else 27042
            self._frida_tunnel.ensure(self.device_serial, local_port, remote_port)

    def _log_frida_start_error(self, startup_log):
        try:
            result = self.adb('shell', f"su -c 'tail -c 2048 {startup_log}'", timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                self.log(result.stdout.strip(), 'error')
        except (subprocess.TimeoutExpired, OSError):
            pass

    def stop_frida_server(self):
        if not self.device_connected:
            return False
        self.stop_bypass()
        command = f"su -c 'pkill -x frida-server; pkill -x {self.stealth_binary_name}'"
        local_port = self.stealth_port if self.stealth_mode else self.frida_local_port
        try:
            # Close this panel's listener before killing the remote endpoint.
            # Keep stale Frida tunnels out of transport recovery snapshots.
            self.adb('forward', '--remove', f'tcp:{local_port}', timeout=3)
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            self.log('Trafik tüneli yenileniyor; Frida cihaz üzerinden durdurulacak.', 'warning')
            self._saved_tunnels = [t for t in self._saved_tunnels
                                   if t[:3] != (self.device_serial, 'forward', f'tcp:{local_port}')]
            if self._tunnel_service:
                self._tunnel_service.context(serial=self.device_serial, devices=self._known_devices,
                                             tunnels=self._saved_tunnels, active=False)
                try:
                    self._tunnel_service.recover()
                except RuntimeError:
                    pass
        try:
            self.adb('shell', command, timeout=3)
        except subprocess.TimeoutExpired:
            if not self.recover_transport(for_action=True):
                raise RuntimeError('Cihaz yanıt vermedi; Frida durdurulduğu doğrulanamadı.')
            self.adb('shell', command, timeout=3)
        deadline = time.monotonic() + 3
        stopped = not self.check_frida_status()
        while not stopped and time.monotonic() < deadline:
            time.sleep(.15)
            stopped = not self.check_frida_status()
        self.log('Frida server durduruldu' if stopped else 'Frida server durdurulamadı', 'success' if stopped else 'error')
        return stopped

    def start_bypass(self, app_package, script_name="nev1.js", start_on_profile=False, instagram_username="", bypass_mode="spawn", deep_cloak=False):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False
        if not self.frida_server_running:
            self.log("Önce Frida server'ı başlatın!", "error")
            return False

        # Security check for script path
        try:
            script_path = self.script_path(script_name)
        except ValueError as e:
            self.log(str(e), "error")
            return False
        if not os.path.exists(script_path):
            self.log(f"Script bulunamadı: {script_name}", "error")
            return False

        # Bundle Deep Cloak (Samsung S25 Ultra Spoof & Anti-Root) if enabled
        if deep_cloak:
            cloak_path = os.path.join(self.project_dir, "device_cloak.js")
            if os.path.exists(cloak_path):
                bundled_path = os.path.join(self.project_dir, "_runtime_bundled.js")
                try:
                    with open(cloak_path, "r", encoding="utf-8") as f1, open(script_path, "r", encoding="utf-8") as f2:
                        bundled_content = f1.read() + "\n\n// Target Script (" + script_name + ")\n" + f2.read()
                    with open(bundled_path, "w", encoding="utf-8") as fb:
                        fb.write(bundled_content)
                    script_path = bundled_path
                    self.log("🛡️ Derin Cihaz & Root Gizleme (Samsung S25 Ultra) modülü eklendi.", "process")
                except Exception as e:
                    self.log(f"Bundle oluşturma uyarısı: {e}", "warning")

        cloak_info = " [🛡️ Samsung S25 Ultra & Anti-Root Cloak: Aktif]" if deep_cloak else ""
        self.log(f"Bypass başlatılıyor: {app_package} (Mod: {bypass_mode}){cloak_info}", "process")
        with self._process_lock:
            if self.bypass_running:
                self.log("Önce çalışan oturumu durdurun.", "warning")
                return False
            self._session_id += 1
            session_id = self._session_id
            self.bypass_running = True

        def task():
            proc = None
            if session_id != self._session_id or not self.bypass_running:
                return
            if bypass_mode == "attach":
                self.log(f"Uygulama başlatılıyor (Attach modu): {app_package}", "process")
                # Force start app via launcher activity
                self.run_adb_cmd(f"shell monkey -p {app_package} -c android.intent.category.LAUNCHER 1")
                # Wait for process initialization
                time.sleep(2.5)

            if app_package == "com.instagram.android" and start_on_profile:
                target_uri = f"instagram://user?username={instagram_username}" if instagram_username else "instagram://profile"
                def launch_profile():
                    # First attempt (early, during startup)
                    time.sleep(4.0)
                    if session_id != self._session_id:
                        return
                    self.log("Profil yönlendirme tetikleniyor (Aşama 1)...", "process")
                    self.run_adb_cmd(f"shell am start -a android.intent.action.VIEW -d {target_uri} -p com.instagram.android")
                    # Second attempt (after cold-start fully finishes, ensuring override bypass)
                    time.sleep(8.0)
                    if session_id != self._session_id:
                        return
                    self.log("Profil yönlendirme tetikleniyor (Aşama 2)...", "process")
                    self.run_adb_cmd(f"shell am start -a android.intent.action.VIEW -d {target_uri} -p com.instagram.android")
                threading.Thread(target=launch_profile, daemon=True).start()

            def safe_decode(b):
                try:
                    return b.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        return b.decode('cp1254')
                    except UnicodeDecodeError:
                        try:
                            return b.decode('cp857')
                        except UnicodeDecodeError:
                            return b.decode('utf-8', errors='ignore')

            try:
                if self.stealth_mode:
                    # Ensure port forwarding is active
                    run_command(f'"{self.adb_path}" forward tcp:{self.stealth_port} tcp:{self.stealth_port}', shell=True, capture_output=True)
                    import socket
                    sock_ok = False
                    try:
                        with socket.create_connection(('127.0.0.1', self.stealth_port), timeout=0.8):
                            sock_ok = True
                    except Exception:
                        sock_ok = False

                    if not sock_ok:
                        self.log(f"Gizlilik sunucusu ({self.stealth_binary_name}) hazırlanıyor...", "process")
                        run_command(f'"{self.adb_path}" shell "su -c \'pkill -9 frida; pkill -9 {self.stealth_binary_name}\'"', shell=True, capture_output=True)
                        self.adb('shell', f"su -c '/data/local/tmp/{self.stealth_binary_name} -l 0.0.0.0:{self.stealth_port} -D </dev/null >/data/local/tmp/{self.stealth_binary_name}-start.log 2>&1'", timeout=3)
                        time.sleep(1.5)
                        run_command(f'"{self.adb_path}" forward tcp:{self.stealth_port} tcp:{self.stealth_port}', shell=True, capture_output=True)

                # Connect through the selected emulator's explicit tunnel;
                # avoid USB discovery against the shared host ADB service.
                frida_port = self.stealth_port if self.stealth_mode else self.frida_local_port
                if not self.stealth_mode:
                    forwarded = self.adb('forward', f'tcp:{frida_port}', 'tcp:27042', timeout=3)
                    if forwarded.returncode != 0:
                        raise RuntimeError(forwarded.stderr.strip() or 'Frida bağlantısı hazırlanamadı.')
                target_args = ["-H", f"127.0.0.1:{frida_port}"]
                cmd = ["frida", *target_args, "-l", script_path, "-N" if bypass_mode == "attach" else "-f", app_package]
                with self._process_lock:
                    if session_id != self._session_id or not self.bypass_running:
                        return
                    proc = subprocess.Popen(cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    self.bypass_process = proc
                for line_bytes in iter(proc.stdout.readline, b''):
                    if line_bytes:
                        line = safe_decode(line_bytes)
                        clean_line = line.strip()
                        lvl = "info"

                        # Match success prefixes (including literal or escaped checkmarks)
                        success_prefixes = ["[✓]", "[+]", "[\\u2713]", "[\u2713]"]
                        is_success = False
                        for prefix in success_prefixes:
                            if prefix in clean_line:
                                lvl = "success"
                                clean_line = clean_line.replace(prefix, "").strip()
                                is_success = True
                                break

                        if not is_success:
                            # Match error prefixes
                            error_prefixes = ["[-]", "[x]", "Error:", "Exception:"]
                            is_error = False
                            for prefix in error_prefixes:
                                if prefix in clean_line:
                                    lvl = "error"
                                    if prefix in ["[-]", "[x]"]:
                                        clean_line = clean_line.replace(prefix, "").strip()
                                    is_error = True
                                    break

                            if not is_error:
                                # Match warning prefixes
                                warning_prefixes = ["[!]", "[*]", "[\\u26a0]", "[\u26a0]"]
                                for prefix in warning_prefixes:
                                    if prefix in clean_line:
                                        lvl = "warning"
                                        clean_line = clean_line.replace(prefix, "").strip()
                                        break

                        self.log(clean_line, lvl)
                    if session_id != self._session_id or not self.bypass_running:
                        break
            except Exception as e:
                self.log(f"Bypass Hatası: {e}", "error")
            finally:
                if proc is not None:
                    self._terminate_process(proc)
                with self._process_lock:
                    if session_id == self._session_id:
                        self.log("Bypass sonlandı", "warning")
                        self.bypass_running = False
                        self.bypass_process = None

        threading.Thread(target=task, daemon=True).start()
        return True

    @staticmethod
    def _terminate_process(proc):
        # Terminate before touching pipes: another thread may be blocked in readline.
        try:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        except OSError:
            pass
        finally:
            if proc.poll() is not None:
                for pipe in (proc.stdout, proc.stdin):
                    if pipe:
                        try:
                            pipe.close()
                        except (OSError, ValueError):
                            pass

    def stop_bypass(self, app_package=None):
        with self._process_lock:
            self.bypass_running = False
            self._session_id += 1
            processes = list(self.extra_processes)
            if self.bypass_process is not None:
                processes.append(self.bypass_process)
            self.extra_processes = []
            self.bypass_process = None
        for proc in processes:
            self._terminate_process(proc)
        if app_package:
            self.run_adb_cmd(f"shell am force-stop {app_package}")
        self.log("Bypass durduruldu", "success")

    def inject_script(self, app_package, script_name, deep_cloak=False):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False

        try:
            script_path = self.script_path(script_name)
        except ValueError as e:
            self.log(str(e), "error")
            return False
        if not os.path.exists(script_path):
            self.log(f"Script bulunamadı: {script_name}", "error")
            return False

        if deep_cloak:
            cloak_path = os.path.join(self.project_dir, "device_cloak.js")
            if os.path.exists(cloak_path):
                bundled_path = os.path.join(self.project_dir, "_runtime_bundled.js")
                try:
                    with open(cloak_path, "r", encoding="utf-8") as f1, open(script_path, "r", encoding="utf-8") as f2:
                        bundled_content = f1.read() + "\n\n// Target Script (" + script_name + ")\n" + f2.read()
                    with open(bundled_path, "w", encoding="utf-8") as fb:
                        fb.write(bundled_content)
                    script_path = bundled_path
                except Exception:
                    pass

        self.log(f"Çalışan uygulamaya ekstra script enjekte ediliyor: {script_name}", "process")

        with self._process_lock:
            session_id = self._session_id

        def task():
            try:
                def safe_decode(b):
                    try:
                        return b.decode('utf-8')
                    except UnicodeDecodeError:
                        try:
                            return b.decode('cp1254')
                        except UnicodeDecodeError:
                            try:
                                return b.decode('cp857')
                            except UnicodeDecodeError:
                                return b.decode('utf-8', errors='ignore')

                self._ensure_frida_tunnel()
                local_port = self.stealth_port if self.stealth_mode else self.frida_local_port
                target_args = ["-H", f"127.0.0.1:{local_port}"]
                cmd = ["frida", *target_args, "-N", app_package, "-l", script_path]
                with self._process_lock:
                    if session_id != self._session_id:
                        return
                    proc = subprocess.Popen(cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, bufsize=0)
                    self.extra_processes.append(proc)

                def read_stdout(p):
                    while session_id == self._session_id and p.poll() is None:
                        line = p.stdout.readline()
                        if not line:
                            break
                        decoded = safe_decode(line)
                        if decoded.strip():
                            clean_line = decoded.strip()
                            lvl = "info"
                            if clean_line.startswith("[*]") or "yüklendi" in clean_line.lower():
                                lvl = "success"
                            elif clean_line.startswith("🔴") or "hata" in clean_line.lower():
                                lvl = "error"
                            elif clean_line.startswith("🟢"):
                                lvl = "success"
                            elif clean_line.startswith("⚪"):
                                lvl = "info"
                            self.log(f"[{script_name}] {clean_line}", lvl)

                threading.Thread(target=read_stdout, args=(proc,), daemon=True).start()
                self.log(f"{script_name} başarıyla enjekte edildi!", "success")
            except Exception as e:
                self.log(f"Enjeksiyon hatası: {e}", "error")

        threading.Thread(target=task, daemon=True).start()
        return True

    def get_running_packages(self):
        if not self.device_connected:
            return []
        processes = self.cache.get('process_table', self.read_process_table, 2, {})
        return sorted({name.split(':')[0] for name in processes
                       if re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+(?:[:][A-Za-z0-9_.]+)?', name)
                       and not name.startswith(('com.android.', 'android.', 'com.google.android.'))})

    def clean_memu_ads(self):
        if not self.device_connected:
            self.log("Reklamları temizlemek için bir cihaz bağlı olmalıdır!", "error")
            return False

        self.log("MEmu reklam ve bloatware hizmetleri analiz ediliyor...", "process")
        ad_packages = [
            "com.microvirt.installer",
            "com.microvirt.guide",
            "com.microvirt.download",
            "com.microvirt.market",
            "com.microvirt.tools"
        ]

        cleaned_count = 0
        for pkg in ad_packages:
            # Force stop and disable the package
            run_command(f'"{self.adb_path}" shell am force-stop {pkg}', shell=True, capture_output=True)
            disable_res = run_command(f'"{self.adb_path}" shell pm disable-user --user 0 {pkg}', shell=True, capture_output=True, text=True, encoding='utf-8')

            if "new state: disabled" in disable_res.stdout or disable_res.returncode == 0:
                self.log(f"{pkg} (Reklam/Bloatware) kalıcı olarak devre dışı bırakıldı.", "success")
                cleaned_count += 1
            else:
                disable_res2 = run_command(f'"{self.adb_path}" shell pm disable {pkg}', shell=True, capture_output=True)
                if disable_res2.returncode == 0:
                    self.log(f"{pkg} kalıcı olarak devre dışı bırakıldı.", "success")
                    cleaned_count += 1
                else:
                    self.log(f"{pkg} devre dışı bırakılamadı (zaten pasif veya bulunamadı).", "info")

        self.log(f"MEmu Reklam Temizliği tamamlandı! {cleaned_count} hizmet pasifleştirildi.", "success")
        return True

    def run_adb_cmd(self, raw_cmd):
        if not self.device_connected:
            self.log("ADB komutu çalıştırmak için bir cihaz bağlı olmalıdır!", "error")
            return "Hata: Cihaz bağlı değil."

        if not isinstance(raw_cmd, str) or not raw_cmd.strip():
            return "Hata: Boş komut."
        cmd = raw_cmd.strip()
        if cmd.startswith("adb "):
            cmd = cmd[4:].strip()
        try:
            # Android shell syntax remains on the device, never the host shell.
            args = [self.adb_path, "shell", cmd[6:].strip()] if cmd.startswith("shell ") else [self.adb_path, *shlex.split(cmd)]
            if len(args) < 2:
                return "Hata: Boş komut."
            self.log(f"Komut gönderildi: adb {cmd}", "terminal")
            result = run_command(args, shell=False, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=30)
            output = result.stdout.strip() or result.stderr.strip() or "[Boş Çıktı]"
            for line in output.split('\n'):
                self.log(f"  {line}", 'info' if result.returncode == 0 else 'error')
            return output if result.returncode == 0 else f'Hata: {output}'
        except Exception as e:
            self.log(f"ADB Hatası: {e}", "error")
            return f"Hata: {e}"

    def install_package_file(self, file_storage):
        if not self.device_connected:
            self.log("APK yüklemek için önce cihaz bağlı olmalıdır!", "error")
            return False, "Cihaz bağlı değil!"

        filename = file_storage.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in [".apk", ".xapk", ".apks"]:
            self.log(f"Desteklenmeyen dosya türü: {filename}", "error")
            return False, "Sadece .apk, .xapk veya .apks dosyaları desteklenir."

        temp_dir = tempfile.mkdtemp(prefix="webmemu-upload-")
        saved_path = os.path.join(temp_dir, "package" + ext)
        try:
            file_storage.save(saved_path)
            file_size_mb = os.path.getsize(saved_path) / (1024 * 1024)
            self.log(f"Dosya alındı: {filename} ({file_size_mb:.2f} MB)", "process")
            if ext == ".apk":
                self.log(f"APK cihaza yükleniyor: {filename}...", "process")
                res = run_command([self.adb_path, "install", "-r", "-d", "-g", saved_path], shell=False, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=120)
                if res.returncode != 0 or "Success" not in res.stdout:
                    res = run_command([self.adb_path, "install", "-r", "-d", saved_path], shell=False, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=120)

                output = (res.stdout + "\n" + res.stderr).strip()
                if "Success" in output or res.returncode == 0:
                    self.log(f"APK Başarıyla yüklendi: {filename}", "success")
                    return True, "APK başarıyla yüklendi!"
                else:
                    self.log(f"APK Yükleme Hatası:\n{output}", "error")
                    return False, f"Yükleme başarısız: {output}"

            elif ext in [".xapk", ".apks"]:
                self.log(f"XAPK/APKS paketi inceleniyor ve açılıyor: {filename}...", "process")
                import zipfile
                extract_path = os.path.join(temp_dir, "extracted_" + str(int(time.time())))
                os.makedirs(extract_path, exist_ok=True)
                with zipfile.ZipFile(saved_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)

                # 1. Check for OBB directory
                obb_dir = os.path.join(extract_path, "Android", "obb")
                if os.path.exists(obb_dir):
                    self.log("OBB verileri cihaza kopyalanıyor (/sdcard/Android/obb)...", "process")
                    run_command([self.adb_path, "push", obb_dir, "/sdcard/Android/"], shell=False, capture_output=True)

                # 2. Find all APK files in extracted folder
                apk_files = []
                for root, _, files in os.walk(extract_path):
                    for f in files:
                        if f.lower().endswith(".apk"):
                            apk_files.append(os.path.join(root, f))

                if not apk_files:
                    self.log("XAPK içinde geçerli APK dosyası bulunamadı!", "error")
                    return False, "XAPK içinde APK bulunamadı."

                if len(apk_files) == 1:
                    self.log(f"Tekli APK yükleniyor: {os.path.basename(apk_files[0])}...", "process")
                    res = run_command([self.adb_path, "install", "-r", "-d", apk_files[0]], shell=False, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=120)
                else:
                    self.log(f"Split (bölünmüş) {len(apk_files)} APK dosyası 'install-multiple' ile yükleniyor...", "process")
                    res = run_command([self.adb_path, "install-multiple", "-r", "-d", *apk_files], shell=False, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=120)

                output = (res.stdout + "\n" + res.stderr).strip()
                if "Success" in output or res.returncode == 0:
                    self.log(f"XAPK paketi başarıyla yüklendi: {filename}", "success")
                    return True, "XAPK paketi başarıyla yüklendi!"
                else:
                    self.log(f"XAPK Yükleme Hatası:\n{output}", "error")
                    return False, f"Yükleme başarısız: {output}"

        except Exception as e:
            self.log(f"Paket yükleme sırasında hata oluştu: {e}", "error")
            return False, str(e)
        finally:
            try:
                import shutil
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass

    def set_proxy(self, proxy_ip='127.0.0.1', proxy_port='8080'):
        if not self.device_connected:
            return False
        address = ipaddress.ip_address(proxy_ip)
        if getattr(address, 'scope_id', None) or not str(proxy_port).isdigit() or not 1 <= int(proxy_port) <= 65535:
            raise ValueError('Geçersiz proxy adresi veya portu.')
        if address.is_loopback:
            reverse = self.adb('reverse', f'tcp:{proxy_port}', f'tcp:{proxy_port}')
            if reverse.returncode != 0:
                raise RuntimeError(reverse.stderr.strip() or 'ADB reverse kurulamadı.')
        host = f'[{address}]' if address.version == 6 else str(address)
        value = f'{host}:{int(proxy_port)}'
        result = self.adb('shell', f'settings put global http_proxy {value} && settings put global global_http_proxy_host {address} && settings put global global_http_proxy_port {int(proxy_port)} && settings get global http_proxy')
        if result.returncode != 0 or result.stdout.strip() != value:
            raise RuntimeError(result.stderr.strip() or 'Proxy ayarı doğrulanamadı.')
        self.proxy_status = value
        self.log(f'Proxy aktif edildi: {value}', 'success')
        return True

    def disable_proxy(self):
        if not self.device_connected:
            return False
        result = self.adb('shell', 'settings delete global http_proxy && settings delete global global_http_proxy_host && settings delete global global_http_proxy_port && settings get global http_proxy')
        if result.returncode != 0 or result.stdout.strip().splitlines()[-1:] != ['null']:
            raise RuntimeError(result.stderr.strip() or 'Proxy kapatılamadı.')
        # Preserve reverse tunnels belonging to other tools.
        port = self.proxy_status.rsplit(':', 1)[-1]
        if port.isdigit():
            self.adb('reverse', '--remove', f'tcp:{port}')
        self.proxy_status = 'Kapatıldı'
        self.log('Proxy kapatıldı.', 'success')
        return True

    def list_packages(self, only_third_party=True):
        friendly_names = {
            "com.instagram.android": "Instagram",
            "com.facebook.katana": "Facebook",
            "com.whatsapp": "WhatsApp",
            "com.twitter.android": "Twitter / X",
            "com.android.chrome": "Google Chrome",
            "org.mozilla.firefox": "Mozilla Firefox",
            "com.spotify.music": "Spotify",
            "com.zhiliaoapp.musically": "TikTok"
        }

        # Fallback list if device is not connected
        fallback_list = [
            {"package": pkg, "name": name} for pkg, name in friendly_names.items()
        ]

        if not self.device_connected:
            return []

        cmd_suffix = " -3" if only_third_party else ""
        cmd = f'pm list packages{cmd_suffix}'
        try:
            result = self.adb('shell', cmd, timeout=5)
            if result.returncode != 0:
                return []

            packages = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith("package:"):
                    pkg = line.split(":", 1)[1]
                    name = friendly_names.get(pkg, pkg)
                    packages.append({"package": pkg, "name": name})

            # Sort packages by friendly name
            packages.sort(key=lambda x: x["name"].lower())

            # If no packages are returned, return fallback list
            if not packages:
                return []
            return packages
        except Exception as e:
            self.log('Uygulama listesi alınamadı; bağlantı kontrol ediliyor.', 'warning')
            # Preserve the last successful list; an unavailable device is not
            # evidence that all its packages have disappeared.
            raise RuntimeError('Uygulama listesi için cihaz yanıt vermedi.') from e

    def clear_app_data(self, app_package):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False, "Cihaz bağlı değil!"
        if not app_package:
            return False, "Uygulama paketi belirtilmedi!"
        self.log(f"{app_package} uygulama verileri ve önbelleği temizleniyor...", "process")
        res = run_command(f'"{self.adb_path}" shell pm clear {app_package}', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if "Success" in res.stdout or res.returncode == 0:
            self.log(f"{app_package} verileri ve önbelleği sıfırlandı (pm clear).", "success")
            return True, "Uygulama verileri ve önbelleği başarıyla temizlendi."
        err = (res.stderr or res.stdout).strip()
        self.log(f"Veri temizleme hatası: {err}", "error")
        return False, err or "Temizlenemedi."

    def restart_app(self, app_package):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False, "Cihaz bağlı değil!"
        if not app_package:
            return False, "Uygulama paketi belirtilmedi!"
        self.log(f"{app_package} yeniden başlatılıyor...", "process")
        run_command(f'"{self.adb_path}" shell am force-stop {app_package}', shell=True, capture_output=True)
        time.sleep(0.5)
        run_command(f'"{self.adb_path}" shell monkey -p {app_package} -c android.intent.category.LAUNCHER 1', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        self.log(f"{app_package} yeniden başlatıldı.", "success")
        return True, "Uygulama yeniden başlatıldı."

    def grant_all_permissions(self, app_package):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False, "Cihaz bağlı değil!"
        if not app_package:
            return False, "Uygulama paketi belirtilmedi!"
        self.log(f"{app_package} izinleri taranıyor ve tanımlanıyor...", "process")

        res = run_command(f'"{self.adb_path}" shell dumpsys package {app_package}', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        lines = res.stdout.splitlines()

        granted_count = 0
        in_requested = False

        for line in lines:
            if "requested permissions:" in line.lower():
                in_requested = True
                continue
            if in_requested:
                if not line.startswith("    ") and not line.startswith("\t"):
                    break
                perm = line.strip().split(":")[0].strip()
                if perm.startswith("android.permission."):
                    g_res = run_command(f'"{self.adb_path}" shell pm grant {app_package} {perm}', shell=True, capture_output=True)
                    if g_res.returncode == 0:
                        granted_count += 1

        if granted_count == 0:
            common_perms = [
                "android.permission.CAMERA",
                "android.permission.RECORD_AUDIO",
                "android.permission.READ_EXTERNAL_STORAGE",
                "android.permission.WRITE_EXTERNAL_STORAGE",
                "android.permission.ACCESS_FINE_LOCATION",
                "android.permission.ACCESS_COARSE_LOCATION",
                "android.permission.READ_CONTACTS",
                "android.permission.POST_NOTIFICATIONS"
            ]
            for p in common_perms:
                g_res = run_command(f'"{self.adb_path}" shell pm grant {app_package} {p}', shell=True, capture_output=True)
                if g_res.returncode == 0:
                    granted_count += 1

        self.log(f"{app_package} için {granted_count} adet izin otomatik olarak verildi.", "success")
        return True, f"{granted_count} adet izin başarıyla verildi."

    def get_app_logcat(self, app_package=None, filter_text=None, lines=150):
        if not self.device_connected:
            return False, 'Cihaz bağlı değil!', []
        def read_logs():
            result = self.adb('shell', 'logcat -d -v threadtime -t 1000', timeout=4)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or 'Logcat okunamadı.')
            return result.stdout.splitlines()
        raw = self.cache.get('logcat', read_logs, 1, [])
        processes = self.cache.get('process_table', self.read_process_table, 2, {}) if app_package else {}
        target_pids = {pid for name, pids in processes.items() if name.split(':')[0] == app_package for pid in pids}
        filtered = []
        for line in raw:
            parts = line.split()
            if app_package and (len(parts) < 3 or parts[2] not in target_pids):
                continue
            if filter_text and filter_text.casefold() not in line.casefold():
                continue
            if line.strip():
                filtered.append(line)
        return True, 'Logcat alındı.', filtered[-lines:]

    def read_process_table(self):
        result = self.adb('shell', 'ps -A', timeout=3)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'İşlem listesi okunamadı.')
        rows = result.stdout.splitlines()
        table = {}
        if not rows:
            return table
        header = rows[0].split()
        pid_index = header.index('PID') if 'PID' in header else 1
        for row in rows[1:]:
            fields = row.split()
            if len(fields) > pid_index and fields[pid_index].isdigit():
                table.setdefault(fields[-1], []).append(fields[pid_index])
        return table

    def clear_logcat(self):
        if not self.device_connected:
            return False
        run_command(f'"{self.adb_path}" shell logcat -c', shell=True, capture_output=True)
        self.log("Logcat arabelleği temizlendi (logcat -c).", "info")
        return True

    def get_cert_hash(self, cert_path):
        stamp = (cert_path, os.stat(cert_path).st_mtime_ns)
        if self._cert_hash_cache and self._cert_hash_cache[0] == stamp:
            return self._cert_hash_cache[1]
        openssl_paths = [
            r"C:\Program Files\Git\usr\bin\openssl.exe",
            r"C:\Program Files\Git\bin\openssl.exe",
            r"C:\Program Files (x86)\Git\bin\openssl.exe",
            "openssl"
        ]

        for openssl in openssl_paths:
            try:
                cmd = f'"{openssl}" x509 -inform PEM -subject_hash_old -in "{cert_path}" -noout'
                res = run_command(cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    value = res.stdout.strip()
                    self._cert_hash_cache = (stamp, value)
                    return value
            except Exception:
                continue

        try:
            from cryptography import x509
            with open(cert_path, "rb") as f:
                cert_bytes = f.read()
            cert = x509.load_pem_x509_certificate(cert_bytes)
            import hashlib
            subject_der = cert.subject.public_bytes()
            md5_hash = hashlib.md5(subject_der).digest()
            val = (md5_hash[0] | (md5_hash[1] << 8) | (md5_hash[2] << 16) | (md5_hash[3] << 24))
            value = f"{val:08x}"
            self._cert_hash_cache = (stamp, value)
            return value
        except Exception as e:
            self.log(f"Hash hesaplama hatası: {e}", "error")
            return None

    def check_cert_status(self, force=False):
        if not self.device_connected:
            return "no_device", False

        now = time.time()
        if not force and (now - self._last_cert_check_time < 8.0):
            return self._cached_cert_status

        try:
            mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
            subject_hash = None
            if os.path.exists(mitm_cert_path):
                subject_hash = self.get_cert_hash(mitm_cert_path)

            if not subject_hash:
                subject_hash = "c8750f0d"

            if not re.fullmatch(r'[0-9a-fA-F]{8}', subject_hash):
                return 'hash_failed', False
            stores = [('/system/etc/security/cacerts', 'installed'),
                      ('/apex/com.android.conscrypt/cacerts', 'installed'),
                      ('/data/misc/user/0/cacerts-added', 'user_installed')]
            patterns = ' '.join(f'{directory}/{subject_hash}.[0-9]*' for directory, _ in stores)
            result = self.adb('shell', f"su -c 'ls {patterns} 2>/dev/null'", timeout=2.5)
            # ls can return nonzero when one store is absent, even if another
            # contains the certificate. Inspect only validated candidate paths.
            candidates = [(path.strip(), state) for path in result.stdout.splitlines()
                          for directory, state in stores
                          if re.fullmatch(re.escape(f'{directory}/{subject_hash}.') + r'\d+', path.strip())]
            expected = None
            if os.path.exists(mitm_cert_path):
                from cryptography import x509
                from cryptography.hazmat.primitives import hashes
                with open(mitm_cert_path, 'rb') as cert_file:
                    expected = x509.load_pem_x509_certificate(cert_file.read()).fingerprint(hashes.SHA256())
            for path, state in candidates[:16]:
                if expected is not None:
                    import base64
                    read = self.adb('shell', f"su -c 'base64 {path}'", timeout=2.5)
                    if read.returncode != 0:
                        return 'unknown', False
                    raw = base64.b64decode(''.join(read.stdout.split()), validate=True)
                    cert = (x509.load_pem_x509_certificate(raw) if b'-----BEGIN CERTIFICATE-----' in raw
                            else x509.load_der_x509_certificate(raw))
                    if cert.fingerprint(hashes.SHA256()) != expected:
                        continue
                elif state == 'user_installed':
                    return 'no_local_cert', False
                self._cached_cert_status = (state, True)
                self._last_cert_check_time = now
                return self._cached_cert_status

            if candidates and expected is not None:
                self._cached_cert_status = ('different_ca', False)
                self._last_cert_check_time = now
                return self._cached_cert_status
            if not candidates and result.returncode != 0 and result.stderr.strip():
                return 'unknown', False

            self._cached_cert_status = ("not_installed", False)
            self._last_cert_check_time = now
            return self._cached_cert_status
        except Exception:
            return "unknown", False

    def download_mitm_cert(self):
        # Check local path first to avoid network timeout
        local_cert = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
        if os.path.exists(local_cert):
            return local_cert

        proxy_host = "127.0.0.1"
        proxy_port = "8080"
        if self.proxy_status and ":" in self.proxy_status:
            parts = self.proxy_status.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                proxy_host = parts[0]
                proxy_port = parts[1]

        temp_pem_path = os.path.join(self.project_dir, "downloaded_mitm_ca.pem")
        try:
            import urllib.request
            proxy_support = urllib.request.ProxyHandler({
                'http': f'http://{proxy_host}:{proxy_port}',
                'https': f'http://{proxy_host}:{proxy_port}'
            })
            opener = urllib.request.build_opener(proxy_support)
            urllib.request.install_opener(opener)

            req = urllib.request.Request("http://mitm.it/cert/pem", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                content = response.read()

            if b"BEGIN CERTIFICATE" in content:
                with open(temp_pem_path, "wb") as f:
                    f.write(content)
                self.log("Sertifika http://mitm.it üzerinden başarıyla indirildi!", "success")
                return temp_pem_path
        except Exception:
            pass
        return None

    def install_mitm_cert(self):
        self._last_cert_check_time = 0
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Sertifika yüklemek için cihaz bağlı olmalıdır!", "error")
            return False, "Cihaz bağlı değil!"

        mitm_cert_path = self.download_mitm_cert()
        if not mitm_cert_path:
            mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")

        if not os.path.exists(mitm_cert_path):
            self.log("Hata: mitmproxy sertifikası bulunamadı (~/.mitmproxy/mitmproxy-ca-cert.pem).", "error")
            return False, "Sertifika dosyası bulunamadı!"

        subject_hash = self.get_cert_hash(mitm_cert_path) or "c8750f0d"
        cert_filename = f"{subject_hash}.0"
        temp_device_path = f"/data/local/tmp/{cert_filename}"
        system_cacerts_path = f"/system/etc/security/cacerts/{cert_filename}"
        user_cacerts_path = f"/data/misc/user/0/cacerts-added/{cert_filename}"

        self.log(f"mitmproxy CA sertifikası ({cert_filename}) yükleniyor...", "process")

        # 1. Push cert to /data/local/tmp
        push_res = run_command(f'"{self.adb_path}" push "{mitm_cert_path}" {temp_device_path}', shell=True, capture_output=True, text=True)
        if push_res.returncode != 0:
            self.log(f"Sertifika cihaza kopyalanamadı: {push_res.stderr}", "error")
            return False, "Dosya kopyalama hatası!"

        # 2. Try root & remount
        run_command(f'"{self.adb_path}" root', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" remount', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /system || mount -o rw,remount /\'"', shell=True, capture_output=True)

        # 3. Copy to System Store & User Store
        install_cmd = (
            f"su -c '"
            f"cp {temp_device_path} {system_cacerts_path}; "
            f"mkdir -p /data/misc/user/0/cacerts-added; "
            f"cp {temp_device_path} {user_cacerts_path}; "
            f"chmod 644 {system_cacerts_path}; "
            f"chmod 644 {user_cacerts_path}; "
            f"chown root:root {system_cacerts_path} 2>/dev/null; "
            f"mount -o ro,remount /system 2>/dev/null'"
        )
        cp_res = run_command(f'"{self.adb_path}" shell "{install_cmd}"', shell=True, capture_output=True, text=True)

        # 4. If direct write failed (overlay/read-only), apply bind-mount trick
        check_res = run_command(f'"{self.adb_path}" shell "ls {system_cacerts_path}"', shell=True, capture_output=True, text=True)
        if cert_filename not in check_res.stdout:
            self.log("Dizin korumalı, bind-mount yöntemi uygulanıyor...", "warning")
            bind_cmds = (
                f"mkdir -p /dev/tmp/cacerts && "
                f"cp /system/etc/security/cacerts/* /dev/tmp/cacerts/ && "
                f"cp {temp_device_path} /dev/tmp/cacerts/ && "
                f"chmod 644 /dev/tmp/cacerts/* && "
                f"mount --bind /dev/tmp/cacerts /system/etc/security/cacerts"
            )
            run_command(f'"{self.adb_path}" shell "su -c \'{bind_cmds}\'"', shell=True, capture_output=True)

        check_final = run_command(f'"{self.adb_path}" shell "ls {system_cacerts_path}"', shell=True, capture_output=True, text=True)
        if cert_filename in check_final.stdout:
            self.log(f"mitmproxy CA sertifikası ({cert_filename}) başarıyla kuruldu!", "success")
            return True, "Sertifika başarıyla yüklendi!"
        else:
            self.log("Sertifika kurulumu tamamlanamadı!", "error")
            return False, "Sertifika yüklenemedi!"

    def remove_mitm_cert(self):
        self._last_cert_check_time = 0
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Sertifika silmek için cihaz bağlı olmalıdır!", "error")
            return False, "Cihaz bağlı değil!"

        mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
        if not os.path.exists(mitm_cert_path):
            self.log("Yerel mitmproxy sertifikası bulunamadı, varsayılan hash 'c8750f0d' kullanılıyor.", "warning")
            subject_hash = "c8750f0d"
        else:
            subject_hash = self.get_cert_hash(mitm_cert_path)
            if not subject_hash:
                subject_hash = "c8750f0d"

        cert_filename = f"{subject_hash}.0"
        system_cacerts_path = f"/system/etc/security/cacerts/{cert_filename}"

        self.log(f"mitmproxy sertifikası ({cert_filename}) kaldırılıyor...", "process")

        # 1. Try unmounting bind mount first
        run_command(f'"{self.adb_path}" shell "su -c \'umount /system/etc/security/cacerts\'"', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" shell "su -c \'rm -rf /dev/tmp/cacerts\'"', shell=True, capture_output=True)

        # 2. Also try direct deletion in case it was written directly
        run_command(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /system\'"', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /\'"', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" shell "su -c \'rm -f {system_cacerts_path}\'"', shell=True, capture_output=True)
        run_command(f'"{self.adb_path}" shell "su -c \'mount -o ro,remount /system\'"', shell=True, capture_output=True)

        # 3. Check if successfully removed
        check_res = run_command(f'"{self.adb_path}" shell "ls {system_cacerts_path}"', shell=True, capture_output=True, text=True)
        if cert_filename not in check_res.stdout:
            self.log("mitmproxy CA sertifikası başarıyla kaldırıldı.", "success")
            return True, "Sertifika kaldırıldı."
        else:
            self.log("Sertifika kaldırılamadı! Sistem dizini korumalı veya mount çözülemedi.", "error")
            return False, "Sertifika silinemedi!"

manager = BypassManager()

@app.before_request
def validate_api_input():
    if request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(success=False, error="JSON nesnesi gereklidir."), 400
        patterns = {"app_package": r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", "instagram_username": r"[A-Za-z0-9_.]+"}
        for key, pattern in patterns.items():
            value = data.get(key, "")
            if not isinstance(value, str) or (value and not re.fullmatch(pattern, value)):
                return jsonify(success=False, error=f"Geçersiz alan: {key}"), 400
        if "proxy_ip" in data:
            try:
                address = ipaddress.ip_address(data["proxy_ip"])
                if getattr(address, "scope_id", None):
                    raise ValueError("Scoped addresses are not supported")
            except (ValueError, TypeError):
                return jsonify(success=False, error="Geçersiz proxy IP adresi."), 400
        if "proxy_port" in data:
            value = str(data["proxy_port"])
            if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
                return jsonify(success=False, error="Geçersiz proxy portu."), 400
        if "content" in data and not isinstance(data["content"], str):
            return jsonify(success=False, error="Script içeriği metin olmalıdır."), 400

def async_action(fn):
    @wraps(fn)
    def accept():
        data = request.get_json(silent=True) or {}
        key = request.path + ':' + json.dumps(data, sort_keys=True)
        @copy_current_request_context
        def work():
            try:
                response = app.make_response(fn())
                result = response.get_json() or {}
                if response.status_code >= 400:
                    result['success'] = False
                return result
            except Exception as exc:
                manager.log(f'İşlem tamamlanamadı: {exc}', 'error')
                return {'success': False, 'error': str(exc)}
            finally:
                manager.cache.invalidate()
        try:
            id = manager.jobs.submit(key, work)
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 429
        return jsonify(success=True, accepted=True, job_id=id), 202
    return accept

@app.route('/api/jobs/<job_id>')
def get_job(job_id):
    job = manager.jobs.get(job_id)
    return (jsonify(job), 200) if job else (jsonify(success=False, error='İşlem bulunamadı.'), 404)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    value = manager.cache.get('status', manager.refresh_status, 1.5, manager.status_snapshot())
    # Session flags change immediately; the device snapshot is refreshed independently.
    value.update(bypass_running=manager.bypass_running, stealth_mode=manager.stealth_mode, api_version=2)
    return jsonify(value)

@app.route('/api/toggle_stealth', methods=['POST'])
@async_action
def toggle_stealth():
    data = request.json or {}
    enabled = bool(data.get("enabled", False))
    changed = (enabled != manager.stealth_mode)
    manager.stealth_mode = enabled
    mode_desc = f"Aktif ({manager.stealth_binary_name} : {manager.stealth_port})" if manager.stealth_mode else "Pasif (Standart frida-server : 27042)"
    manager.log(f"🥷 Gizlilik Modu (Stealth Mode): {mode_desc}", "info")

    if changed and manager.frida_server_running:
        manager.start_frida_server()

    return jsonify({
        "success": True,
        "stealth_mode": manager.stealth_mode,
        "stealth_port": manager.stealth_port,
        "stealth_binary_name": manager.stealth_binary_name
    })

@app.route('/api/install_cert', methods=['POST'])
@async_action
def install_cert():
    success, msg = manager.install_mitm_cert()
    return jsonify({"success": success, "message": msg})

@app.route('/api/remove_cert', methods=['POST'])
@async_action
def remove_cert():
    success, msg = manager.remove_mitm_cert()
    return jsonify({"success": success, "message": msg})

@app.route('/api/install_apk', methods=['POST'])
def install_apk():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Yüklenecek dosya seçilmedi."}), 400
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({"success": False, "error": "Geçersiz dosya adı."}), 400

    success, msg = manager.install_package_file(file)
    return jsonify({"success": success, "message": msg})

@app.route('/api/log_frontend_error', methods=['POST'])
def log_frontend_error():
    data = request.json or {}
    msg = data.get("message")
    line = data.get("lineno")
    err = data.get("error")
    manager.log(f"FRONTEND HATA: {msg} (Satır: {line})", "error")
    if err:
        for l in err.split('\n'):
            manager.log(f"  {l}", "error")
    return jsonify({"success": True})

@app.route('/api/check_devices', methods=['POST'])
@async_action
def check_devices():
    connected = manager.check_devices()
    return jsonify(success=connected, **manager.status_snapshot())

@app.route('/api/start_memu', methods=['POST'])
@async_action
def start_memu():
    success, msg = manager.start_memu()
    return jsonify({"success": success, "message": msg})

@app.route('/api/start_mitmweb', methods=['POST'])
@async_action
def start_mitmweb():
    success, msg = manager.start_mitmweb()
    return jsonify({"success": success, "message": msg})

@app.route('/api/start_frida', methods=['POST'])
@async_action
def start_frida():
    success = manager.start_frida_server()
    return jsonify(success=success, status="running" if success else "failed")

@app.route('/api/stop_frida', methods=['POST'])
@async_action
def stop_frida():
    success = manager.stop_frida_server()
    return jsonify(success=success, status="stopped" if success else "failed")

@app.route('/api/start_bypass', methods=['POST'])
@async_action
def start_bypass():
    data = request.json or {}
    app_package = data.get("app_package", "com.instagram.android")
    script_name = data.get("script_name", "nev1.js")
    start_on_profile = data.get("start_on_profile", False)
    instagram_username = data.get("instagram_username", "")
    bypass_mode = data.get("bypass_mode", "spawn")
    deep_cloak = data.get("deep_cloak", False)

    success = manager.start_bypass(app_package, script_name, start_on_profile, instagram_username, bypass_mode, deep_cloak)
    return jsonify({"success": success, "status": "running" if success else "failed"})

@app.route('/api/stop_bypass', methods=['POST'])
@async_action
def stop_bypass():
    data = request.json or {}
    app_package = data.get("app_package", "")
    manager.stop_bypass(app_package)
    return jsonify({"success": True, "status": "stopped"})

@app.route('/api/inject_script', methods=['POST'])
@async_action
def inject_script():
    data = request.json or {}
    app_package = data.get("app_package", "com.instagram.android")
    script_name = data.get("script_name", "")
    deep_cloak = data.get("deep_cloak", False)
    if not script_name:
        return jsonify({"success": False, "error": "Script adı belirtilmelidir."})
    success = manager.inject_script(app_package, script_name, deep_cloak)
    return jsonify({"success": success})

@app.route('/api/list_running_packages', methods=['GET'])
def list_running_packages():
    pkgs = manager.get_running_packages()
    return jsonify({"success": True, "packages": pkgs})

@app.route('/api/force_stop_package', methods=['POST'])
@async_action
def force_stop_package():
    data = request.json or {}
    app_package = data.get("app_package", "")
    if app_package:
        manager.run_adb_cmd(f"shell pm disable-user --user 0 {app_package}")
        manager.run_adb_cmd(f"shell pm enable {app_package}")
        manager.log(f"{app_package} sonlandırıldı ve arka plan/recent listesinden temizlendi.", "success")
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Geçersiz paket"})

@app.route('/api/clean_ads', methods=['POST'])
@async_action
def clean_ads():
    success = manager.clean_memu_ads()
    return jsonify({"success": success})

@app.route('/api/quick_action/clear_data', methods=['POST'])
@async_action
def quick_clear_data():
    data = request.json or {}
    app_package = data.get("app_package", "")
    success, msg = manager.clear_app_data(app_package)
    return jsonify({"success": success, "message": msg})

@app.route('/api/quick_action/restart_app', methods=['POST'])
@async_action
def quick_restart_app():
    data = request.json or {}
    app_package = data.get("app_package", "")
    success, msg = manager.restart_app(app_package)
    return jsonify({"success": success, "message": msg})

@app.route('/api/quick_action/grant_permissions', methods=['POST'])
@async_action
def quick_grant_permissions():
    data = request.json or {}
    app_package = data.get("app_package", "")
    success, msg = manager.grant_all_permissions(app_package)
    return jsonify({"success": success, "message": msg})

@app.route('/api/get_logcat', methods=['POST'])
def get_logcat():
    data = request.json or {}
    app_package = data.get("app_package", "")
    filter_text = data.get("filter_text", "")
    try:
        lines = int(data.get('lines', 150))
        if not 1 <= lines <= 1000:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(success=False, error='Satır sayısı 1–1000 arasında olmalıdır.'), 400
    success, msg, logs = manager.get_app_logcat(app_package, filter_text, lines)
    return jsonify({"success": success, "message": msg, "logs": logs})

@app.route('/api/clear_logcat', methods=['POST'])
@async_action
def clear_logcat():
    success = manager.clear_logcat()
    return jsonify({"success": success})

@app.route('/api/list_scripts', methods=['GET'])
def list_scripts():
    pattern = os.path.join(manager.project_dir, "*.js")
    js_files = glob.glob(pattern)
    basenames = [os.path.basename(f) for f in js_files if not os.path.basename(f).startswith("_")]
    return jsonify({"success": True, "scripts": basenames})

@app.route('/api/list_packages', methods=['GET'])
def list_packages():
    only_third_party = request.args.get("third_party", "true").lower() == "true"
    packages = manager.cache.get(f'packages-{only_third_party}', lambda: manager.list_packages(only_third_party), 20, [])
    key = f'packages-{only_third_party}'
    with manager.cache.lock:
        refreshing = key in manager.cache.busy
    return jsonify({'success': True, 'packages': packages if manager.device_connected else [], 'refreshing': refreshing})

@app.route('/api/read_script', methods=['GET'])
def read_script():
    script_name = request.args.get("script_name", "nev1.js")
    try:
        script_path = manager.script_path(script_name)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    if not os.path.exists(script_path):
        return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404

    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"success": True, "content": content})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/save_script', methods=['POST'])
def save_script():
    data = request.json or {}
    script_name = data.get("script_name", "nev1.js")
    content = data.get("content", "")

    try:
        script_path = manager.script_path(script_name)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    try:
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(content)
        manager.log(f"Script kaydedildi: {script_name}", "success")
        return jsonify({"success": True})
    except Exception as e:
        manager.log(f"Script kaydedilemedi: {e}", "error")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/run_adb', methods=['POST'])
@async_action
def run_adb():
    data = request.json or {}
    cmd = data.get("command", "")
    if not cmd:
        return jsonify({"success": False, "error": "Boş komut"})

    output = manager.run_adb_cmd(cmd)
    return jsonify({"success": not output.startswith("Hata:"), "output": output, "error": output if output.startswith("Hata:") else None})

@app.route('/api/toggle_proxy', methods=['POST'])
@async_action
def toggle_proxy():
    data = request.json or {}
    enabled = data.get("enabled", False)
    proxy_ip = data.get("proxy_ip", "127.0.0.1")
    proxy_port = data.get("proxy_port", "8080")

    if enabled:
        success = manager.set_proxy(proxy_ip, proxy_port)
    else:
        success = manager.disable_proxy()

    return jsonify({"success": success, "proxy_status": manager.proxy_status})

@app.route('/api/get_logs', methods=['GET'])
def get_logs():
    with manager._log_lock:
        return jsonify({"success": True, "logs": list(manager.log_history), "revision": manager.log_revision})

@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    with manager._log_lock:
        manager.log_history.clear()
        manager.log_revision += 1
    manager.log("Log geçmişi temizlendi.", "info")
    return jsonify({"success": True})

if __name__ == '__main__':
    # Start checking status on launch
    stop_monitor = threading.Event()
    monitor = threading.Thread(target=manager.watch_connection, args=(stop_monitor,), daemon=True,
                               name='adb-status-monitor')
    monitor.start()
    # Run server locally on port 5000
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True, use_reloader=False)
    finally:
        stop_monitor.set()
        manager._adb_service.close()
        manager._tunnel_service.close()
        manager._frida_tunnel.close()
        manager._proxy_tunnels.close()
