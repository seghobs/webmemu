"""Regression coverage for MEmu retaining a daemon's ADB shell streams."""
import subprocess
import unittest
from unittest.mock import Mock, patch
import app as web


class FridaStartTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            self.manager = web.BypassManager(command_executor=web.run_command)
        self.manager.check_devices = Mock(return_value=True)
        self.manager.install_frida_server = Mock(return_value=True)
        self.manager.log = Mock()
        self.manager.adb = Mock(return_value=Mock(returncode=0, stdout='', stderr=''))

    def test_cold_start_detaches_all_streams_and_checks_process(self):
        self.manager.check_frida_status = Mock(side_effect=[False, True])
        self.assertTrue(self.manager.start_frida_server())
        command = self.manager.adb.call_args.args[1]
        self.assertIn('</dev/null', command)
        self.assertIn('>/data/local/tmp/frida-server-start.log 2>&1', command)
        self.assertEqual(self.manager.check_frida_status.call_count, 2)

    def test_timeout_with_running_daemon_is_reconciled(self):
        self.manager.check_frida_status = Mock(side_effect=[False, True])
        self.manager.adb.side_effect = subprocess.TimeoutExpired('adb', 3)
        self.assertTrue(self.manager.start_frida_server())

    def test_timeout_without_daemon_fails_and_reads_diagnostics(self):
        self.manager.check_frida_status = Mock(return_value=False)
        self.manager.adb.side_effect = [subprocess.TimeoutExpired('adb', 3),
                                       Mock(returncode=0, stdout='startup failed', stderr='')]
        with patch.object(web.time, 'monotonic', side_effect=[0, 0, 4]), patch.object(web.time, 'sleep'):
            self.assertFalse(self.manager.start_frida_server())
        self.manager.log.assert_any_call('startup failed', 'error')

    def test_nonzero_exit_is_failure_even_if_another_process_exists(self):
        self.manager.check_frida_status = Mock(side_effect=[False, True])
        self.manager.adb.side_effect = [Mock(returncode=1, stdout='', stderr='launch failed'),
                                       Mock(returncode=0, stdout='Address already in use', stderr='')]
        self.assertFalse(self.manager.start_frida_server())
        self.manager.log.assert_any_call('Address already in use', 'error')

    def test_running_server_does_not_launch_again(self):
        self.manager.check_frida_status = Mock(return_value=True)
        self.assertTrue(self.manager.start_frida_server())
        self.manager.adb.assert_not_called()

    def test_stop_timeout_recovers_then_verifies_process_absent(self):
        self.manager.device_connected = True
        self.manager.stop_bypass = Mock()
        self.manager.recover_transport = Mock(return_value=True)
        self.manager.adb.side_effect = [Mock(returncode=0), subprocess.TimeoutExpired('adb', 3), Mock(returncode=0)]
        self.manager.check_frida_status = Mock(return_value=False)
        self.assertTrue(self.manager.stop_frida_server())
        self.manager.recover_transport.assert_called_once_with(for_action=True)
        self.assertEqual(self.manager.adb.call_count, 3)

    def test_stop_failed_recovery_never_reports_success(self):
        self.manager.device_connected = True
        self.manager.stop_bypass = Mock()
        self.manager.recover_transport = Mock(return_value=False)
        self.manager.adb.side_effect = [Mock(returncode=0), subprocess.TimeoutExpired('adb', 3)]
        with self.assertRaisesRegex(RuntimeError, 'doğrulanamadı'):
            self.manager.stop_frida_server()
        self.assertEqual(self.manager.adb.call_count, 2)

    def test_stop_removes_owned_tunnel_before_remote_process(self):
        self.manager.device_connected = True
        self.manager.stop_bypass = Mock()
        self.manager.check_frida_status = Mock(return_value=False)
        self.assertTrue(self.manager.stop_frida_server())
        calls = self.manager.adb.call_args_list
        self.assertEqual(calls[0].args, ('forward', '--remove', 'tcp:27043'))
        self.assertEqual(calls[1].args[0], 'shell')

    def test_stop_waits_for_remote_process_to_exit(self):
        self.manager.device_connected = True
        self.manager.stop_bypass = Mock()
        self.manager.check_frida_status = Mock(side_effect=[True, False])
        with patch.object(web.time, 'sleep'):
            self.assertTrue(self.manager.stop_frida_server())
        self.assertEqual(self.manager.check_frida_status.call_count, 2)

    def test_stuck_tunnel_cannot_block_remote_stop(self):
        self.manager.device_connected = True
        self.manager.device_serial = '127.0.0.1:21503'
        self.manager.stop_bypass = Mock()
        self.manager._tunnel_service = Mock()
        self.manager._saved_tunnels = [('127.0.0.1:21503', 'forward', 'tcp:27043', 'tcp:27042')]
        self.manager.adb.side_effect = [subprocess.TimeoutExpired('adb', 3), Mock(returncode=0)]
        self.manager.check_frida_status = Mock(return_value=False)
        self.assertTrue(self.manager.stop_frida_server())
        self.manager._tunnel_service.recover.assert_called_once()
        self.assertEqual(self.manager._saved_tunnels, [])
        self.assertEqual(self.manager.adb.call_args.args[0], 'shell')

    def test_stealth_launch_keeps_port_and_dedicated_log(self):
        self.manager.stealth_mode = True
        self.manager.check_frida_status = Mock(side_effect=[False, True])
        self.assertTrue(self.manager.start_frida_server())
        command = self.manager.adb.call_args_list[0].args[1]
        self.assertIn('-l 0.0.0.0:13337', command)
        self.assertIn('sys_agent-start.log', command)
        self.manager.adb.assert_any_call('forward', 'tcp:13337', 'tcp:13337')
