import concurrent.futures
import socket
import struct
import threading
import time
import unittest
from adb_tunnel import AdbTcpTunnel, AUTH, CNXN, OPEN, OKAY, WRTE, CLSE, read_packet, send_packet, read_exact


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeDevice:
    def __init__(self, auth=False):
        self.auth = auth
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(8)
        self.listener.settimeout(.1)
        self.stop = threading.Event()
        self.requests = []
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while not self.stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self.client, args=(sock,), daemon=True).start()

    def client(self, sock):
        try:
            with sock:
                sock.settimeout(2)
                kind, _, _, _ = read_packet(sock)
                if kind != CNXN:
                    return
                if self.auth:
                    send_packet(sock, AUTH, 1, 0, b'challenge')
                    return
                send_packet(sock, CNXN, 0x01000001, 4096, b'device::')
                kind, local, _, service = read_packet(sock)
                self.requests.append(service)
                if kind != OPEN:
                    return
                send_packet(sock, OKAY, 7, local)
                while True:
                    kind, _, _, data = read_packet(sock)
                    if kind == WRTE:
                        send_packet(sock, OKAY, 7, local)
                        send_packet(sock, WRTE, 7, local, data)
                    elif kind == CLSE:
                        send_packet(sock, CLSE, 7, local)
                        return
        except (OSError, EOFError):
            pass

    def close(self):
        self.stop.set()
        self.listener.close()


class DirectTunnelTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.addCleanup(self.device.close)
        self.tunnel = AdbTcpTunnel()
        self.addCleanup(self.tunnel.close)
        self.port = unused_port()
        self.tunnel.ensure(f'127.0.0.1:{self.device.port}', self.port, 27042)

    def test_binary_streams_are_independent_and_preserved(self):
        def echo(marker):
            data = bytes(range(256))*64 + marker
            with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
                client.settimeout(3)
                client.sendall(data)
                self.assertEqual(read_exact(client, len(data)), data)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(echo, [b'one', b'two', b'three']))
        self.assertEqual(self.device.requests, [b'tcp:27042\0']*3)

    def test_removing_one_listener_keeps_other_clients_alive(self):
        second = unused_port()
        self.tunnel.ensure(f'127.0.0.1:{self.device.port}', second, 13337)
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as one, \
             socket.create_connection(('127.0.0.1', second), timeout=3) as two:
            for client in (one, two):
                client.settimeout(3)
                client.sendall(b'first')
                self.assertEqual(read_exact(client, 5), b'first')
            self.tunnel.remove(self.port)
            self.assertEqual(one.recv(1), b'')
            two.sendall(b'still alive')
            self.assertEqual(read_exact(two, 11), b'still alive')

    def test_authentication_challenge_is_rejected(self):
        auth = FakeDevice(auth=True)
        self.addCleanup(auth.close)
        self.tunnel.ensure(f'127.0.0.1:{auth.port}', self.port, 27042)
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
            self.assertEqual(client.recv(1), b'')
        deadline = time.monotonic()+1
        while not self.tunnel.errors and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue(any('kimlik doğrulaması' in e for e in self.tunnel.errors))
        self.assertEqual(auth.requests, [])

    def test_packet_length_and_magic_are_validated(self):
        for length, magic in ((2*1024*1024, WRTE^0xffffffff), (0, 0)):
            left, right = socket.socketpair()
            with left, right:
                right.sendall(struct.pack('<6I', WRTE, 7, 1, length, 0, magic))
                with self.assertRaises(ValueError):
                    read_packet(left)

    def test_remote_device_addresses_are_not_allowed(self):
        with self.assertRaises(ValueError):
            self.tunnel.ensure('192.168.1.2:5555', unused_port(), 27042)

    def test_stopped_listener_rejects_clients_then_restarts_without_rebinding(self):
        original = self.tunnel.listeners[self.port][0]
        self.tunnel.remove(self.port)
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
            self.assertEqual(client.recv(1), b'')
        self.tunnel.ensure(f'127.0.0.1:{self.device.port}', self.port, 27042)
        self.assertIs(self.tunnel.listeners[self.port][0], original)
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
            client.sendall(b'restarted')
            self.assertEqual(read_exact(client, 9), b'restarted')
