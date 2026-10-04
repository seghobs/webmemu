import subprocess
import unittest
from unittest.mock import Mock, patch
import app as web


class AdbRoutingTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            self.m = web.BypassManager(command_executor=web.run_command)
        self.m.device_serial = '127.0.0.1:21503'
        self.m._known_devices = [self.m.device_serial]
        self.m._adb_service = Mock()
        self.m._adb_service.run.return_value = subprocess.CompletedProcess([], 0, 'ok', '')
        self.m._tunnel_service = Mock()
        self.m._tunnel_serial = self.m.device_serial
        self.m._tunnel_service.run.return_value = subprocess.CompletedProcess([], 0, 'ok', '')

    def test_legacy_remote_shell_stays_one_argument_without_host_shell(self):
        command = f'"{self.m.adb_path}" shell "su -c \'echo one; echo two\'"'
        self.m.run_external_adb(command, shell=True, text=True)
        args = self.m._adb_service.run.call_args.args[0]
        self.assertEqual(args[-2:], ['shell', "su -c 'echo one; echo two'"])
        self.assertEqual(args[1:3], ['-s', self.m.device_serial])

    def test_legacy_upload_keeps_windows_filename_with_spaces(self):
        self.m.run_external_adb(f'"{self.m.adb_path}" push "C:\\My Files\\server" /data/local/tmp/server')
        self.assertEqual(self.m._adb_service.run.call_args.args[0][-3:],
                         ['push', 'C:\\My Files\\server', '/data/local/tmp/server'])

    def test_global_adb_calls_route_to_service_without_subprocess_run(self):
        with patch.object(web, 'manager', self.m), patch.object(web.subprocess, 'run') as direct:
            web.run_command([self.m.adb_path, 'install', '-r', 'demo.apk'], timeout=120)
            direct.assert_not_called()
            self.assertEqual(self.m._adb_service.run.call_args.kwargs['timeout'], 120)

    def test_tunnel_changes_remembered_immediately(self):
        self.m.adb('reverse', 'tcp:8080', 'tcp:8080')
        self.assertEqual(self.m._saved_tunnels, [(self.m.device_serial, 'reverse', 'tcp:8080', 'tcp:8080')])
        self.m.adb('reverse', '--remove', 'tcp:8080')
        self.assertEqual(self.m._saved_tunnels, [])

    def test_tunnel_traffic_never_uses_device_control_service(self):
        self.m.adb('forward', 'tcp:27043', 'tcp:27042')
        self.m._adb_service.run.assert_not_called()
        self.m._tunnel_service.run.assert_called_once()
        self.assertEqual(self.m._adb_service.context.call_args.kwargs['tunnels'], [])

    def test_shell_commands_never_use_tunnel_service(self):
        self.m.adb('shell', 'ps -A')
        self.m._adb_service.run.assert_called_once()
        self.m._tunnel_service.run.assert_not_called()

    def test_control_job_does_not_disable_tunnel_recovery(self):
        self.m.jobs.running = Mock(return_value=True)
        self.m.adb('forward', 'tcp:27043', 'tcp:27042')
        self.assertTrue(self.m._adb_service.context.call_args.kwargs['active'])
        self.assertFalse(self.m._tunnel_service.context.call_args.kwargs['active'])

    def test_frida_forward_uses_direct_protocol_without_windows_server(self):
        self.m._frida_tunnel = Mock()
        self.m.adb('forward', 'tcp:27043', 'tcp:27042')
        self.m._frida_tunnel.ensure.assert_called_once_with(self.m.device_serial, 27043, 27042)
        self.m._adb_service.run.assert_not_called()
        self.m._tunnel_service.run.assert_not_called()

    def test_proxy_reverse_uses_direct_protocol_without_windows_server(self):
        self.m._proxy_tunnels = Mock()
        self.m.adb('reverse', 'tcp:8080', 'tcp:8080')
        self.m._proxy_tunnels.ensure.assert_called_once_with(self.m.device_serial, 8080, 8080)
        self.m._adb_service.run.assert_not_called()
        self.m._tunnel_service.run.assert_not_called()

    def test_owned_forward_list_never_starts_windows_data_server(self):
        from adb_tunnel import AdbTcpTunnel
        self.m._frida_tunnel = AdbTcpTunnel()
        self.m._frida_tunnel.listeners[27043] = (None, ('127.0.0.1', 21503, 27042), True)
        self.m._saved_tunnels = [('127.0.0.1:21503', 'forward', 'tcp:27043', 'tcp:27042')]
        result = self.m.adb('forward', '--list')
        self.assertIn('127.0.0.1:21503 tcp:27043 tcp:27042', result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.m._adb_service.run.assert_not_called()
        self.m._tunnel_service.run.assert_not_called()

    def test_supervisor_recovery_does_not_claim_accepted_as_success(self):
        self.assertFalse(self.m.recover_adb())
        self.m._adb_service.recover.assert_called_once()

    def test_active_state_sent_to_independent_supervisor(self):
        self.m.bypass_running = True
        self.m.adb('devices')
        self.assertTrue(self.m._adb_service.context.call_args.kwargs['active'])

    def test_non_adb_command_stays_outside_adb_worker(self):
        with patch.object(web, 'manager', self.m), patch.object(web.subprocess, 'run') as direct:
            web.run_command(['openssl', 'version'])
            direct.assert_called_once()
            self.m._adb_service.run.assert_not_called()
