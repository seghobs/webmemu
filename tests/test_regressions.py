import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
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
        self.client.environ_base['HTTP_X_WEBMEMU_TOKEN'] = web.app.config['API_TOKEN']

    def test_untrusted_host_cannot_read_panel_or_write_script(self):
        for path in ('/', '/api/get_logs'):
            response = self.client.get(path, base_url='http://untrusted.example:5000')
            self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/save_script', base_url='http://untrusted.example:5000',
                                    json={'script_name':'blocked.js','content':'// blocked'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Path(self.temp.name, 'blocked.js').exists())

    def test_untrusted_origin_is_rejected_even_with_valid_token(self):
        for origin in ('http://evil.example', 'null', 'http://localhost:9999'):
            response = self.client.post('/api/clear_logs', headers={'Origin':origin})
            self.assertEqual(response.status_code, 403)

    def test_api_requires_token_for_reads_json_and_uploads(self):
        client = web.app.test_client()
        self.assertEqual(client.get('/api/get_logs').status_code, 403)
        self.assertEqual(client.post('/api/clear_logs', json={}).status_code, 403)
        self.assertEqual(client.post('/api/install_apk', data={
            'file':(io.BytesIO(b'apk'),'test.apk')}).status_code, 403)
        self.assertEqual(self.client.get('/api/get_logs', headers={
            'X-WebMEmu-Token':'wrong'}).status_code, 403)

    def test_local_panel_supplies_token_and_disables_caching(self):
        for base in ('http://localhost', 'http://127.0.0.1:5000', 'http://[::1]:5000'):
            response = self.client.get('/', base_url=base)
            self.assertEqual(response.status_code, 200)
            self.assertIn(web.app.config['API_TOKEN'], response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(self.client.get('/api/get_logs',base_url=base,
                headers={'Origin':base}).status_code, 200)

    def force_stop_result(self):
        response = self.client.post('/api/force_stop_package', json={'app_package':'com.example.app'})
        self.assertEqual(response.status_code, 202)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = self.manager.jobs.get(response.json['job_id'])
            if job['state'] in ('completed','failed'):
                return job
            time.sleep(.005)
        self.fail('Force-stop job did not finish')

    def test_force_stop_uses_am_without_changing_package_enabled_state(self):
        with patch.object(self.manager, 'run_adb_cmd', return_value='[Boş Çıktı]') as run:
            job = self.force_stop_result()
        self.assertEqual(job['state'], 'completed')
        self.assertTrue(job['result']['success'])
        run.assert_called_once_with('shell am force-stop com.example.app')

    def test_force_stop_adb_failure_is_failed_job(self):
        with patch.object(self.manager, 'run_adb_cmd', return_value='Hata: device offline'):
            job = self.force_stop_result()
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(job['result']['success'])
        self.assertIn('device offline', job['result']['error'])
        self.assertFalse(any('sonlandırıldı' in line for line in self.manager.log_history))

    def test_force_stop_disconnected_device_never_reports_success(self):
        self.manager.device_connected = False
        job = self.force_stop_result()
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(job['result']['success'])

    def test_stop_mitmweb_terminates_owned_process_and_verifies_ports(self):
        process = Mock()
        process.poll.return_value = None
        self.manager._mitmweb_process = process
        with patch.object(web.os, 'name', 'posix'), patch.object(web.socket, 'create_connection', side_effect=ConnectionRefusedError):
            ok, _ = self.manager.stop_mitmweb()
        self.assertTrue(ok)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        self.assertIsNone(self.manager._mitmweb_process)

    def test_stop_mitmweb_refuses_unidentified_port_owner(self):
        with patch.object(web.os, 'name', 'nt'), patch.object(web, 'run_command', return_value=Mock(returncode=1, stderr='Port sahibi doğrulanamadı')):
            ok, message = self.manager.stop_mitmweb()
        self.assertFalse(ok)
        self.assertIn('doğrulanamadı', message)

    def test_mitmweb_reuses_running_server_without_launch_or_extra_tab(self):
        with patch.object(self.manager, '_mitmweb_ready', return_value=True), patch.object(web.subprocess, 'Popen') as launch, patch('webbrowser.open') as browser:
            ok, message = self.manager.start_mitmweb()
        self.assertTrue(ok)
        self.assertIn('zaten', message)
        launch.assert_not_called()
        browser.assert_not_called()

    def test_mitmweb_busy_port_does_not_launch_duplicate(self):
        with patch.object(self.manager, '_mitmweb_ready', return_value=False), patch.object(web.socket, 'create_connection'), patch.object(web.subprocess, 'Popen') as launch:
            ok, message = self.manager.start_mitmweb()
        self.assertFalse(ok)
        self.assertIn('8080', message)
        launch.assert_not_called()

    def test_mitmweb_exit_is_reported_as_failure_without_opening_browser(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(self.manager, '_mitmweb_ready', return_value=False), patch.object(web.socket, 'create_connection', side_effect=ConnectionRefusedError), patch.object(web.shutil, 'which', return_value='mitmweb.exe'), patch.object(web.subprocess, 'Popen', return_value=process), patch('webbrowser.open') as browser:
            ok, message = self.manager.start_mitmweb()
        self.assertFalse(ok)
        self.assertIn('kapandı', message)
        browser.assert_not_called()
        self.assertFalse(any('başarıyla başlatıldı' in line for line in self.manager.log_history))

    def test_mitmweb_launch_waits_for_readiness_then_opens_browser(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(self.manager, '_mitmweb_ready', side_effect=[False, False, True]) as ready, patch.object(web.socket, 'create_connection', side_effect=ConnectionRefusedError), patch.object(web.shutil, 'which', return_value='mitmweb.exe'), patch.object(web.subprocess, 'Popen', return_value=process) as launch, patch.object(web.time, 'sleep'), patch('webbrowser.open') as browser:
            ok, _ = self.manager.start_mitmweb()
        self.assertTrue(ok)
        self.assertEqual(ready.call_count, 3)
        self.assertFalse(launch.call_args.kwargs['shell'])
        browser.assert_called_once()
        process.terminate.assert_not_called()

    def test_mitmweb_readiness_authenticates_to_existing_ui(self):
        response = Mock()
        response.read.return_value = b'<title>mitmproxy</title>'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(web.urllib.request, 'build_opener', return_value=opener), patch.object(web.socket, 'create_connection'):
            self.assertTrue(self.manager._mitmweb_ready())
        opener.open.assert_called_once_with('http://127.0.0.1:8081/?token=123456', timeout=.5)

    def test_mitmweb_readiness_rejects_unrelated_http_service(self):
        response = Mock()
        response.read.return_value = b'<title>Other server</title>'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(web.urllib.request, 'build_opener', return_value=opener), patch.object(web.socket, 'create_connection') as connect:
            self.assertFalse(self.manager._mitmweb_ready())
        connect.assert_not_called()

    def stop_frida_result(self):
        response = self.client.post('/api/stop_frida', json={})
        self.assertEqual(response.status_code, 202)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = self.manager.jobs.get(response.json['job_id'])
            if job['state'] in ('completed', 'failed'):
                return job['result']
            time.sleep(.005)
        self.fail('Stop job did not finish')

    def test_stopping_frida_automatically_disables_proxy_after_stop(self):
        calls = []
        with patch.object(self.manager, 'stop_frida_server', side_effect=lambda: calls.append('stop') or True), patch.object(self.manager, 'disable_proxy', side_effect=lambda: calls.append('proxy_off') or True), patch.object(self.manager, 'stop_mitmweb', side_effect=lambda: (calls.append('mitmweb_off') or True, 'stopped')):
            result = self.stop_frida_result()
        self.assertTrue(result['success'])
        self.assertEqual(calls, ['stop', 'proxy_off', 'mitmweb_off'])
        self.assertFalse(result['mitmweb_running'])

    def test_failed_frida_stop_does_not_disable_proxy(self):
        with patch.object(self.manager, 'stop_frida_server', return_value=False), patch.object(self.manager, 'disable_proxy') as disable, patch.object(self.manager, 'stop_mitmweb') as stop_mitmweb:
            result = self.stop_frida_result()
        self.assertFalse(result['success'])
        disable.assert_not_called()
        stop_mitmweb.assert_not_called()

    def test_frida_stop_reports_proxy_cleanup_failure(self):
        for outcome in (False, RuntimeError('device offline')):
            with self.subTest(outcome=outcome):
                with patch.object(self.manager, 'stop_frida_server', return_value=True), patch.object(self.manager, 'disable_proxy', side_effect=outcome if isinstance(outcome, Exception) else None, return_value=False):
                    result = self.stop_frida_result()
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'stopped')
                self.assertIn('proxy kapatılamadı', result['error'])

    def test_frida_stop_reports_mitmweb_stop_failure(self):
        with patch.object(self.manager, 'stop_frida_server', return_value=True), patch.object(self.manager, 'disable_proxy', return_value=True), patch.object(self.manager, 'stop_mitmweb', return_value=(False, 'Port sahibi doğrulanamadı')):
            result = self.stop_frida_result()
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'stopped')
        self.assertIn('Mitmweb durdurulamadı', result['error'])

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
