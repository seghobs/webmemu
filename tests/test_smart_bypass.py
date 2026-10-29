import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
from smart_bypass import inspect_apks, SmartBypass

class SmartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def apk(self, name='base.apk', **entries):
        path = self.root/name
        with zipfile.ZipFile(path, 'w') as archive:
            for key, value in entries.items(): archive.writestr(key,value)
        return path

    def test_java_plan_detects_okhttp_and_does_not_claim_success(self):
        plan = inspect_apks([self.apk(**{'classes.dex':b'Lokhttp3/CertificatePinner;'})])
        self.assertIn('okhttp',plan['signals'])
        self.assertEqual(plan['script'],'universal_unpin.js')
        self.assertNotIn('verified',plan)

    def test_native_flutter_in_split_prevents_guessed_memory_patching(self):
        plan = inspect_apks([self.apk(**{'classes.dex':b'java'}),
                            self.apk('split.apk',**{'lib/arm64-v8a/libflutter.so':b'flutter'})])
        self.assertIsNone(plan['script'])
        self.assertIn('flutter',plan['signals'])

    def test_cronet_and_xamarin_are_explicitly_unsupported(self):
        for entry in ['lib/x86/libcronet.so','lib/x86/libmonodroid.so']:
            self.assertIsNone(inspect_apks([self.apk(**{entry:b'lib'})])['script'])

    def controller(self):
        controller = SmartBypass(Mock())
        controller.nonce = 'current'
        controller.update(phase='starting',started_at=100)
        return controller

    def test_only_current_session_http_response_can_verify(self):
        c = self.controller()
        c.event('SMART_EVENT:old:'+json.dumps({'kind':'https_response','host':'example.com','code':200}))
        self.assertNotEqual(c.snapshot()['phase'],'verified')
        c.event('SMART_EVENT:current:'+json.dumps({'kind':'observer_ready','hooks':3}))
        self.assertEqual(c.state['phase'],'waiting')
        c.event('SMART_EVENT:current:'+json.dumps({'kind':'https_response','host':'example.com','code':200}))
        self.assertEqual(c.snapshot()['phase'],'verified')
        c.ended()
        c.event('SMART_EVENT:current:'+json.dumps({'kind':'https_response','host':'example.com','code':200}))
        self.assertEqual(c.snapshot()['phase'],'stopped')

    def test_invalid_events_and_plain_success_logs_are_not_evidence(self):
        c = self.controller()
        for line in ['[✓] Universal SSL Pinning bypass aktif!', 'SMART_EVENT:current:bad',
                     'SMART_EVENT:current:'+json.dumps({'kind':'https_response','host':'example.com','code':0}),
                     'SMART_EVENT:current:'+json.dumps({'kind':'https_response','host':'bad host','code':200})]:
            c.event(line)
        self.assertNotEqual(c.snapshot()['phase'],'verified')
        with patch('smart_bypass.time.time',return_value=200):
            self.assertEqual(c.snapshot()['phase'],'unverified')

    def test_active_session_is_not_replaced(self):
        manager = Mock(bypass_running=True)
        c = SmartBypass(manager)
        self.assertFalse(c.start('com.example.app','127.0.0.1','8080')['success'])
        manager.adb.assert_not_called()

    def test_unsupported_plan_never_starts_frida_or_proxy(self):
        m = Mock(bypass_running=False, project_dir=str(self.root))
        def adb(*args, **kwargs):
            if args[0]=='shell' and args[1].startswith('pm path'):
                return Mock(returncode=0,stdout='package:/data/app/test/base.apk')
            if args[0]=='shell': return Mock(returncode=0,stdout='10')
            with zipfile.ZipFile(args[2], 'w') as archive: archive.writestr('lib/x86/libflutter.so',b'lib')
            return Mock(returncode=0)
        m.adb.side_effect = adb
        c = SmartBypass(m)
        result = c.start('com.example.app','127.0.0.1','8080')
        self.assertFalse(result['success'])
        self.assertEqual(c.snapshot()['phase'],'unsupported')
        m.start_frida_server.assert_not_called()
        m.set_proxy.assert_not_called()

    def test_prepare_start_is_not_verification_and_evidence_saves_apk_profile(self):
        m = Mock(bypass_running=False, project_dir=str(self.root))
        m.check_cert_status.return_value = ('installed',True)
        m.start_mitmweb.return_value = (True,'ready')
        (self.root/'_smart_observer.js').write_text('// observer',encoding='utf-8')
        (self.root/'.mitmproxy').mkdir()
        (self.root/'.mitmproxy/mitmproxy-ca-cert.pem').write_bytes(b'public certificate')
        def adb(*args, **kwargs):
            if args[0]=='shell' and args[1].startswith('pm path'):
                return Mock(returncode=0,stdout='package:/data/app/test/base.apk')
            if args[0]=='shell': return Mock(returncode=0,stdout='10')
            with zipfile.ZipFile(args[2], 'w') as archive: archive.writestr('classes.dex',b'Lokhttp3/CertificatePinner;')
            return Mock(returncode=0)
        m.adb.side_effect = adb
        c = SmartBypass(m)
        with patch('smart_bypass.Path.home',return_value=self.root):
            result = c.start('com.example.app','127.0.0.1','8080')
        self.assertTrue(result['success'])
        self.assertEqual(result['smart']['phase'],'starting')
        self.assertIn('SMART_NONCE',m.start_bypass.call_args.kwargs['smart_source'])
        c.event('SMART_EVENT:'+c.nonce+':'+json.dumps({'kind':'https_response','host':'example.com','code':200}))
        self.assertEqual(c.snapshot()['phase'],'verified')
        profile = json.loads((self.root/'artifacts/smart-profiles/com.example.app.json').read_text())
        self.assertEqual(profile['apk_sha256'],c.state['plan']['apk_sha256'])
        self.assertNotIn('host',profile)

    def test_apk_change_invalidates_fingerprint(self):
        first = inspect_apks([self.apk(**{'classes.dex':b'version one'})])
        second = inspect_apks([self.apk(**{'classes.dex':b'version two'})])
        self.assertNotEqual(first['apk_sha256'],second['apk_sha256'])

if __name__ == '__main__': unittest.main()
