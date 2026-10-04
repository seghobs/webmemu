import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
import app as web


def certificate():
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'mitmproxy')])
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=365)).sign(key, None))


class CertStatusTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            self.m = web.BypassManager(command_executor=Mock())
        self.m.device_connected = True
        self.m.get_cert_hash = Mock(return_value='c8750f0d')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ca = certificate()
        self.path = Path(self.temp.name)/'ca.pem'
        self.path.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))
        patcher = patch.object(web.os.path, 'expanduser', return_value=str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def probe(self, store, ca, suffix='0', listing_code=0):
        data = base64.b64encode(ca.public_bytes(serialization.Encoding.DER)).decode()
        self.m.adb = Mock(side_effect=[
            subprocess.CompletedProcess([], listing_code, f'{store}/c8750f0d.{suffix}\n', ''),
            subprocess.CompletedProcess([], 0, data, '')])
        return self.m.check_cert_status(force=True)

    def test_matching_user_ca_is_installed_even_when_other_store_is_missing(self):
        self.assertEqual(self.probe('/data/misc/user/0/cacerts-added', self.ca, listing_code=1),
                         ('user_installed', True))

    def test_same_subject_different_ca_is_reported_as_mismatch(self):
        self.assertEqual(self.probe('/data/misc/user/0/cacerts-added', certificate()), ('different_ca', False))

    def test_hash_collision_suffix_and_system_store_are_supported(self):
        self.assertEqual(self.probe('/system/etc/security/cacerts', self.ca, suffix='1'), ('installed', True))

    def test_unreadable_certificate_is_unknown_not_missing(self):
        self.m.adb = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, '/data/misc/user/0/cacerts-added/c8750f0d.0', ''),
            subprocess.CompletedProcess([], 1, '', 'Permission denied')])
        self.assertEqual(self.m.check_cert_status(force=True), ('unknown', False))


if __name__ == '__main__':
    unittest.main()
