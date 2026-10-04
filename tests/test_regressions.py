import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from werkzeug.datastructures import FileStorage
import app as web


class RegressionTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            self.manager = web.BypassManager(command_executor=web.run_command)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager.project_dir = self.temp.name
        self.manager.device_connected = True
        self.manager_patch = patch.object(web, 'manager', self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.client = web.app.test_client()

    def test_host_shell_is_never_used_for_adb_input(self):
        with patch.object(web.subprocess, 'run', return_value=Mock(stdout='', stderr='')) as run:
            self.manager.run_adb_cmd('devices & echo marker')
            self.assertFalse(run.call_args.kwargs['shell'])
            self.assertIsInstance(run.call_args.args[0], list)
            self.manager.run_adb_cmd('shell echo hello; echo world')
            self.assertEqual(run.call_args.args[0][-1], 'echo hello; echo world')
            self.assertFalse(run.call_args.kwargs['shell'])

    def test_legacy_shell_fields_reject_injection(self):
        for route, body in [('/api/quick_action/restart_app', {'app_package':'x.y & echo marker'}), ('/api/toggle_proxy', {'enabled':True, 'proxy_port':'8080 & echo marker'})]:
            self.assertEqual(self.client.post(route, json=body).status_code, 400)

    def test_script_api_restricts_file_types_and_paths(self):
        for name in ['app.py', 'run_web.bat', '../test.js', 'C:\\test.js', '_runtime_bundled.js']:
            self.assertEqual(self.client.post('/api/save_script', json={'script_name':name,'content':'test'}).status_code, 400)
            self.assertEqual(self.client.get('/api/read_script', query_string={'script_name':name}).status_code, 400)
        self.assertEqual(self.client.post('/api/save_script', json={'script_name':'test.js','content':'// test'}).status_code, 200)
        self.assertEqual(self.client.get('/api/read_script?script_name=test.js').json['content'], '// test')

    def test_upload_ignores_client_path_and_cleans_only_own_directory(self):
        seen = []
        def install(args, **kwargs):
            self.assertFalse(kwargs['shell'])
            target = Path(args[-1])
            self.assertEqual(target.name, 'package.apk')
            self.assertTrue(target.exists())
            seen.append(target.parent)
            return Mock(returncode=0, stdout='Success', stderr='')
        with patch.object(web.subprocess, 'run', side_effect=install):
            for _ in range(2):
                ok, _ = self.manager.install_package_file(FileStorage(stream=io.BytesIO(b'apk'), filename='../escape.apk'))
                self.assertTrue(ok)
        self.assertNotEqual(seen[0], seen[1])
        self.assertTrue(all(not p.exists() for p in seen))
        self.assertFalse((Path(self.temp.name)/'escape.apk').exists())

    def test_log_revision_keeps_changing_at_capacity(self):
        for i in range(100): self.manager.log(str(i))
        old = self.client.get('/api/get_logs').json
        self.manager.log('latest')
        new = self.client.get('/api/get_logs').json
        self.assertEqual(len(new['logs']), 100)
        self.assertGreater(new['revision'], old['revision'])
        self.assertIn('latest', new['logs'][-1])
        self.assertFalse(hasattr(self.manager, 'log_queue'))
        self.client.post('/api/clear_logs')
        self.assertGreater(self.client.get('/api/get_logs').json['revision'], new['revision'])

    def test_cert_probe_is_read_only_and_checks_trust_stores(self):
        with patch.object(web.os.path, 'exists', return_value=False), patch.object(web.subprocess, 'run', return_value=Mock(returncode=1, stdout='', stderr='')) as run:
            self.assertEqual(self.manager.check_cert_status(force=True), ('not_installed',False))
            self.assertEqual(run.call_count, 1)
            self.assertNotIn('cp ', run.call_args.args[0])
        with patch.object(web.os.path, 'exists', return_value=False), patch.object(web.subprocess, 'run', return_value=Mock(returncode=0, stdout='/system/etc/security/cacerts/c8750f0d.0')):
            self.assertEqual(self.manager.check_cert_status(force=True), ('installed',True))

    def test_stop_cancels_pending_start(self):
        Path(self.temp.name, 'test.js').write_text('// test')
        self.manager.frida_server_running = True
        with patch.object(web.threading, 'Thread') as thread, patch.object(web.subprocess, 'Popen') as popen:
            self.assertTrue(self.manager.start_bypass('com.example.app', 'test.js'))
            pending_task = thread.call_args.kwargs['target']
            self.manager.stop_bypass()
            pending_task()
            popen.assert_not_called()
            self.assertFalse(self.manager.bypass_running)

    def test_frida_uses_selected_device_tunnel_without_usb_discovery(self):
        Path(self.temp.name, 'test.js').write_text('// test')
        self.manager.frida_server_running = True
        proc = Mock(stdout=io.BytesIO(b''))
        self.manager.adb = Mock(return_value=Mock(returncode=0, stderr=''))
        self.manager._terminate_process = Mock()
        with patch.object(web.threading, 'Thread') as thread, patch.object(web.subprocess, 'Popen', return_value=proc) as popen:
            self.assertTrue(self.manager.start_bypass('com.example.app', 'test.js', bypass_mode='attach'))
            thread.call_args.kwargs['target']()
            self.manager.adb.assert_called_once_with('forward', 'tcp:27043', 'tcp:27042', timeout=3)
            command = popen.call_args.args[0]
            self.assertEqual(command[1:3], ['-H', '127.0.0.1:27043'])
            self.assertNotIn('-U', command)

    def test_stop_unblocks_reader_without_killing_other_processes(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdout=subprocess.PIPE, stdin=subprocess.PIPE)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        reader = threading.Thread(target=proc.stdout.readline, daemon=True)
        reader.start()
        self.manager.bypass_process = proc
        self.manager.bypass_running = True
        with patch.object(web.subprocess, 'run') as run:
            self.manager.stop_bypass()
            run.assert_not_called()
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertIsNotNone(proc.poll())
        self.assertFalse(self.manager.bypass_running)
        self.assertIsNone(self.manager.bypass_process)

if __name__ == '__main__':
    unittest.main()
