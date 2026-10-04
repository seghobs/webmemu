"""Desired-state loopback proxy tunnels over adbd, without Windows adb servers."""
import itertools
import re
import select
import socket
import threading
from adb_tunnel import CNXN, OPEN, OKAY, WRTE, CLSE, AUTH, read_packet, send_packet


class ReverseEndpoint:
    def __init__(self, serial, source, destination):
        match = re.fullmatch(r'127\.0\.0\.1:([0-9]{1,5})', serial or '')
        if not match or not 0 < int(match[1]) < 65536:
            raise ValueError('Yerel MEmu TCP cihazı gereklidir.')
        if not all(0 < port < 65536 for port in (source, destination)):
            raise ValueError('Geçersiz proxy tüneli portu.')
        self.address = ('127.0.0.1', int(match[1]))
        self.source, self.destination = source, destination
        self.stop = threading.Event()
        self.ready_event = threading.Event()
        self.ready = False
        self.error = None
        self.device = None
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.channels = {}
        self.ids = itertools.count(2)
        self.maxdata = 4096
        self.last_open = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def send(self, command, arg0, arg1, payload=b'', expected=None):
        with self.send_lock:
            if not self.device or (expected is not None and expected is not self.device):
                raise EOFError('Proxy cihaz bağlantısı kapandı.')
            send_packet(self.device, command, arg0, arg1, payload)

    def _run(self):
        while not self.stop.is_set():
            try:
                with socket.create_connection(self.address, timeout=5) as device:
                    self.device = device
                    device.settimeout(5)
                    self.send(CNXN, 0x01000000, 4096, b'host::\0')
                    kind, _, maxdata, _ = read_packet(device)
                    if kind == AUTH:
                        self.stop.set()
                        raise ValueError('Cihaz kimlik doğrulaması istiyor; proxy tüneli açılmadı.')
                    if kind != CNXN or not maxdata:
                        raise ValueError('Geçersiz proxy cihaz bağlantısı.')
                    self.maxdata = min(maxdata, 65536)
                    command = f'reverse:forward:tcp:{self.source};tcp:{self.destination}\0'.encode()
                    self.send(OPEN, 1, 0, command)
                    kind, admin_id, local_id, _ = read_packet(device)
                    if kind != OKAY or local_id != 1:
                        raise ValueError('Proxy tüneli isteği kabul edilmedi.')
                    pending = []
                    while True:
                        packet = read_packet(device)
                        kind, sender, recipient, data = packet
                        if recipient == 1:
                            if kind != WRTE or sender != admin_id or not data.startswith(b'OKAY'):
                                raise ValueError('Cihaz proxy dinleyicisini açamadı.')
                            break
                        if len(pending) >= 32:
                            raise ValueError('Proxy tüneli başlangıç kuyruğu doldu.')
                        pending.append(packet)
                    self.send(OKAY, 1, admin_id)
                    self.ready, self.error = True, None
                    self.ready_event.set()
                    while not self.stop.is_set():
                        if pending:
                            kind, sender, recipient, data = pending.pop(0)
                        else:
                            if not select.select([device], [], [], .25)[0]:
                                continue
                            kind, sender, recipient, data = read_packet(device)
                        if recipient == 1:
                            if kind == CLSE:
                                self.send(CLSE, 1, sender)
                            elif kind == WRTE:
                                self.send(OKAY, 1, sender)
                            continue
                        if kind == OPEN:
                            self.last_open = data.decode('utf-8','replace')[:80]
                            if data.rstrip(b'\0') != f'tcp:{self.destination}'.encode():
                                self.send(CLSE, 0, sender)
                                continue
                            with self.lock:
                                if len(self.channels) >= 32:
                                    self.send(CLSE, 0, sender)
                                    continue
                                local_id = next(self.ids)
                                channel = dict(remote=sender, socket=None, ack=threading.Event(),
                                               device=device, opened=False)
                                channel['ack'].set()
                                self.channels[local_id] = channel
                            threading.Thread(target=self._host, args=(local_id, channel), daemon=True).start()
                            continue
                        with self.lock:
                            channel = self.channels.get(recipient)
                        if not channel or sender != channel['remote']:
                            if kind != CLSE:
                                self.send(CLSE, 0, sender)
                            continue
                        if kind == OKAY:
                            channel['ack'].set()
                        elif kind == WRTE:
                            host = channel['socket']
                            if not host:
                                self._close_channel(recipient, notify=True)
                                continue
                            try:
                                host.sendall(data)
                                self.send(OKAY, recipient, sender)
                            except OSError:
                                self._close_channel(recipient, notify=True)
                        elif kind == CLSE:
                            self._close_channel(recipient, notify=True)
            except (OSError, EOFError, ValueError) as exc:
                self.error = str(exc)
            finally:
                self.ready = False
                self.ready_event.clear()
                self.device = None
                with self.lock:
                    for local_id in list(self.channels):
                        self._close_channel(local_id)
            # Only tunnel configuration is restored. Application bytes and
            # HTTP requests are never saved or replayed after a disconnection.
            self.stop.wait(1)

    def _host(self, local_id, channel):
        try:
            with socket.create_connection(('127.0.0.1', self.destination), timeout=2) as host:
                host.settimeout(5)
                with self.lock:
                    if self.channels.get(local_id) is not channel:
                        return
                    channel['socket'] = host
                channel['opened'] = True
                self.send(OKAY, local_id, channel['remote'], expected=channel['device'])
                while not self.stop.is_set():
                    if not channel['ack'].wait(5):
                        break
                    if not select.select([host], [], [], .25)[0]:
                        with self.lock:
                            if self.channels.get(local_id) is not channel:
                                return
                        continue
                    data = host.recv(self.maxdata)
                    if not data:
                        break
                    channel['ack'].clear()
                    self.send(WRTE, local_id, channel['remote'], data, expected=channel['device'])
        except (OSError, EOFError):
            pass
        finally:
            self._close_channel(local_id, notify=True)

    def _close_channel(self, local_id, notify=False):
        with self.lock:
            channel = self.channels.pop(local_id, None)
            if not channel:
                return
            channel['ack'].set()
            host = channel['socket']
            if host:
                try:
                    host.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                host.close()
        if notify:
            try:
                self.send(CLSE, local_id if channel['opened'] else 0, channel['remote'], expected=channel['device'])
            except (OSError, EOFError):
                pass

    def close(self):
        self.stop.set()
        device = self.device
        if device:
            try:
                device.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            device.close()


class AdbReverseTunnels:
    def __init__(self):
        self.lock = threading.RLock()
        self.endpoints = {}

    def ensure(self, serial, source, destination):
        key = (serial, source)
        with self.lock:
            endpoint = self.endpoints.get(key)
            if endpoint and endpoint.destination != destination:
                endpoint.close()
                endpoint = None
            created = endpoint is None
            if created:
                if len(self.endpoints) >= 8 and key not in self.endpoints:
                    raise ValueError('Proxy tüneli sınırına ulaşıldı.')
                endpoint = ReverseEndpoint(serial, source, destination)
                self.endpoints[key] = endpoint
                endpoint.thread.start()
        if not endpoint.ready_event.wait(6) or not endpoint.ready:
            if created:
                with self.lock:
                    if self.endpoints.get(key) is endpoint:
                        del self.endpoints[key]
                endpoint.close()
            raise RuntimeError(endpoint.error or 'Proxy tüneli bağlantı süresi aşıldı.')

    def remove(self, serial, source):
        with self.lock:
            endpoint = self.endpoints.pop((serial, source), None)
        if endpoint:
            endpoint.close()

    def rows(self, serial):
        with self.lock:
            return [(device, source, endpoint.destination) for (device, source), endpoint in self.endpoints.items()
                    if device == serial and endpoint.ready]

    def close(self):
        with self.lock:
            endpoints = list(self.endpoints.values())
            self.endpoints.clear()
        for endpoint in endpoints:
            endpoint.close()
