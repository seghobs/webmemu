"""Loopback TCP forwarding directly over MEmu's adbd protocol.

Each client has its own device connection and bounded I/O. Frida traffic never
enters the Windows adb server. Only local, already trusted MEmu TCP devices are
supported; authentication challenges are rejected, never bypassed.
"""
import re
import select
import socket
import struct
import threading
import time

CNXN, OPEN, OKAY, WRTE, CLSE, AUTH = [int.from_bytes(s, 'little') for s in
                                    (b'CNXN', b'OPEN', b'OKAY', b'WRTE', b'CLSE', b'AUTH')]


def send_packet(sock, command, arg0, arg1, payload=b''):
    sock.sendall(struct.pack('<6I', command, arg0, arg1, len(payload), sum(payload) & 0xffffffff,
                             command ^ 0xffffffff) + payload)


def read_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        data = sock.recv(count-len(chunks))
        if not data:
            raise EOFError('Cihaz tüneli kapandı.')
        chunks.extend(data)
    return bytes(chunks)


def read_packet(sock):
    command, arg0, arg1, length, checksum, magic = struct.unpack('<6I', read_exact(sock, 24))
    if magic != command ^ 0xffffffff or length > 1024 * 1024:
        raise ValueError('Geçersiz ADB paketi.')
    payload = read_exact(sock, length)
    if checksum and checksum != sum(payload) & 0xffffffff:
        raise ValueError('ADB paket sağlama toplamı geçersiz.')
    return command, arg0, arg1, payload


class AdbTcpTunnel:
    def __init__(self):
        self.lock = threading.RLock()
        self.listeners = {}
        self.clients = set()
        self.errors = []

    def ensure(self, serial, local_port, remote_port):
        match = re.fullmatch(r'127\.0\.0\.1:([0-9]{1,5})', serial or '')
        if not match or not 0 < int(match[1]) < 65536:
            raise ValueError('Doğrudan tünel için yerel MEmu TCP cihazı gereklidir.')
        if not 0 < local_port < 65536 or not 0 < remote_port < 65536:
            raise ValueError('Geçersiz tünel portu.')
        with self.lock:
            old = self.listeners.get(local_port)
            target = ('127.0.0.1', int(match[1]), remote_port)
            if old:
                if old[1] != target:
                    self.remove(local_port)
                self.listeners[local_port] = (old[0], target, True)
                return
            listener = socket.socket()
            try:
                listener.bind(('127.0.0.1', local_port))
                listener.listen(8)
                listener.settimeout(.25)
            except BaseException:
                listener.close()
                raise
            self.listeners[local_port] = (listener, target, True)
            threading.Thread(target=self._accept, args=(listener, target, local_port), daemon=True).start()

    def _accept(self, listener, target, port):
        while True:
            try:
                local, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                current = self.listeners.get(port)
                if not current or current[0] is not listener:
                    local.close()
                    return
                if not current[2]:
                    local.close()
                    continue
                target = current[1]
                self.clients.add((local, port))
            threading.Thread(target=self._client, args=(local, target, port), daemon=True).start()

    def _client(self, local, target, port):
        try:
            with local, socket.create_connection(target[:2], timeout=5) as device:
                local.settimeout(5)
                device.settimeout(5)
                with self.lock:
                    self.clients.add((device, port))
                try:
                    send_packet(device, CNXN, 0x01000000, 4096, b'host::\0')
                    kind, version, maxdata, _ = read_packet(device)
                    if kind == AUTH:
                        raise ValueError('Cihaz ADB kimlik doğrulaması istiyor; doğrudan tünel açılmadı.')
                    if kind != CNXN or maxdata == 0:
                        raise ValueError('Cihaz ADB bağlantısını kabul etmedi.')
                    send_packet(device, OPEN, 1, 0, f'tcp:{target[2]}\0'.encode())
                    kind, remote_id, local_id, _ = read_packet(device)
                    if kind != OKAY or local_id != 1:
                        raise ValueError('Frida cihaz portuna bağlanılamadı.')
                    pending = False
                    pending_until = 0
                    while True:
                        if pending and time.monotonic() >= pending_until:
                            raise TimeoutError('ADB tüneli yazma onayı süresi aşıldı.')
                        readable, _, _ = select.select([device] if pending else [device, local], [], [], 1)
                        for source in readable:
                            if source is local:
                                data = local.recv(min(maxdata, 65536))
                                if not data:
                                    send_packet(device, CLSE, 1, remote_id)
                                    return
                                send_packet(device, WRTE, 1, remote_id, data)
                                pending = True
                                pending_until = time.monotonic() + 5
                            else:
                                kind, sender, recipient, data = read_packet(device)
                                if sender != remote_id or recipient != 1:
                                    raise ValueError('Geçersiz ADB kanal kimliği.')
                                if kind == WRTE:
                                    local.sendall(data)
                                    send_packet(device, OKAY, 1, remote_id)
                                elif kind == OKAY:
                                    pending = False
                                elif kind == CLSE:
                                    send_packet(device, CLSE, 1, remote_id)
                                    return
                                else:
                                    raise ValueError('Beklenmeyen ADB kanal paketi.')
                finally:
                    with self.lock:
                        self.clients.discard((device, port))
        except (OSError, EOFError, ValueError) as exc:
            with self.lock:
                self.errors = (self.errors + [str(exc)])[-5:]
        finally:
            with self.lock:
                self.clients.discard((local, port))

    def remove(self, port):
        with self.lock:
            old = self.listeners.get(port)
            if old:
                self.listeners[port] = (old[0], old[1], False)
            for sock, bound_port in list(self.clients):
                if bound_port == port:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()

    def close(self):
        with self.lock:
            for port in list(self.listeners):
                self.remove(port)
                self.listeners.pop(port)[0].close()
