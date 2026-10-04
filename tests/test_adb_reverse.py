import socket
import unittest
from adb_reverse import ReverseEndpoint, AdbReverseTunnels


class ReverseSafetyTests(unittest.TestCase):
    def test_only_local_device_and_valid_ports(self):
        for serial, source, destination in [('10.0.0.1:5555', 8080, 8080),
                                            ('127.0.0.1:0', 8080, 8080),
                                            ('127.0.0.1:21503', 0, 8080),
                                            ('127.0.0.1:21503', 8080, 65536)]:
            with self.subTest(serial=serial, source=source, destination=destination):
                with self.assertRaises(ValueError):
                    ReverseEndpoint(serial, source, destination)

    def test_old_connection_cannot_send_on_new_transport(self):
        endpoint = ReverseEndpoint('127.0.0.1:21503', 8080, 8080)
        old, old_peer = socket.socketpair()
        current, current_peer = socket.socketpair()
        try:
            endpoint.device = current
            with self.assertRaises(EOFError):
                endpoint.send(b'WRTE', 2, 7, b'private request', expected=old)
            current_peer.settimeout(.05)
            with self.assertRaises(socket.timeout):
                current_peer.recv(1)
        finally:
            for sock in (old, old_peer, current, current_peer):
                sock.close()

    def test_removal_is_idempotent(self):
        tunnels = AdbReverseTunnels()
        tunnels.remove('127.0.0.1:21503', 8080)
        self.assertEqual(tunnels.rows('127.0.0.1:21503'), [])
        tunnels.close()
