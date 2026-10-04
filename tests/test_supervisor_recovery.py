import base64
import os
import unittest
from unittest.mock import MagicMock, Mock, patch
from adb_service import Supervisor


def reply(text='', code=0):
    return {'returncode': code, 'stdout': base64.b64encode(text.encode()).decode(), 'stderr': ''}


class SupervisorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.s = Supervisor('configured-adb', os.getpid(), 'test-token')
        self.s.context = {'serial': '127.0.0.1:21503', 'devices': ['127.0.0.1:21503'],
                          'tunnels': [('127.0.0.1:21503', 'reverse', 'tcp:8080', 'tcp:8080')]}
        self.s._command = Mock(return_value=reply('WEBMEMU_READY'))

    def test_stuck_shutdown_rechecks_owner_and_restores_tunnels(self):
        def command(args, **kwargs):
            return {'error': 'timeout'} if args == ['kill-server'] else reply('WEBMEMU_READY')
        self.s._command.side_effect = command
        with patch('adb_service.owned_server', return_value=True) as owner:
            self.s._recover(True)
            owner.assert_called_with('configured-adb', terminate=True)
            self.s._command.assert_any_call(['reverse', 'tcp:8080', 'tcp:8080'], serial='127.0.0.1:21503')
            self.assertEqual(self.s.state['server_restarts'], 1)
            self.assertTrue(self.s.state['device_ready'])

    def test_equal_clock_times_cannot_corrupt_priority_queue(self):
        requests = [dict(op='run', token='test-token', argv=['configured-adb', 'shell', cmd], timeout=1)
                    for cmd in ('pm list packages -3', 'ps -A', 'settings get global http_proxy')]
        with patch('adb_service.time.monotonic_ns', return_value=123), \
             patch('adb_service._read_message', side_effect=requests), \
             patch('adb_service._write_message'), \
             patch('adb_service.threading.Event') as event:
            event.return_value.wait.return_value = True
            for _ in requests:
                self.s._handle(MagicMock())
        self.assertNotIn('last_ipc_error', self.s.state)
        items = [self.s.queue.get_nowait() for _ in requests]
        self.assertEqual([item[2]['argv'][-1] for item in items],
                         [req['argv'][-1] for req in requests])
        self.assertEqual(len({item[1] for item in items}), len(requests))

    def test_interactive_read_command_precedes_background_probe(self):
        requests = [dict(op='run', token='test-token', argv=['configured-adb', 'shell', 'ps -A'], timeout=3),
                    dict(op='run', token='test-token', argv=['configured-adb', 'shell', 'pm list packages -3'],
                         timeout=3, interactive=True)]
        with patch('adb_service._read_message', side_effect=requests), \
             patch('adb_service._write_message'), patch('adb_service.threading.Event') as event:
            event.return_value.wait.return_value = True
            for _ in requests:
                self.s._handle(MagicMock())
        self.assertTrue(self.s.queue.get_nowait()[2]['interactive'])

    def test_health_tolerates_observed_two_second_device_latency(self):
        self.s._command.return_value = reply('WEBMEMU_READY')
        self.s._health()
        self.s._command.assert_any_call(['devices'], timeout=5)
        self.s._command.assert_any_call(['shell', 'echo WEBMEMU_READY'], serial='127.0.0.1:21503', timeout=5)
        self.assertTrue(self.s.state['device_ready'])

    def test_requested_recovery_is_not_starved_by_pending_commands(self):
        self.s.request_recovery.set()
        self.s.queue.put((0, 0, {}, Mock(), {}, 0))
        self.s._health = Mock(side_effect=self.s.stop.set)
        with patch('adb_service._parent_alive', return_value=True), patch('builtins.print'):
            self.s.serve()
        self.s._health.assert_called_once()

    def test_foreign_server_never_stopped(self):
        with patch('adb_service.owned_server', return_value=False):
            self.s._recover(True)
            self.s._command.assert_not_called()

    def test_observed_second_device_prevents_reset_even_if_flask_is_stale(self):
        self.s.observed_devices = ['127.0.0.1:21503', 'physical-device']
        with patch('adb_service.owned_server') as owner:
            self.s._recover(True)
            owner.assert_not_called()
            self.s._command.assert_not_called()

    def test_active_session_prevents_reset(self):
        self.s.context['active'] = True
        self.s._recover(True)
        self.s._command.assert_not_called()

    def test_cooldown_bounds_restart_attempts(self):
        with patch('adb_service.owned_server', return_value=True) as owner:
            self.s._recover(True)
            self.s._recover(True)
            self.assertEqual(owner.call_count, 1)

    def test_device_reconnect_does_not_delay_host_recovery(self):
        self.s._recover(False)
        with patch('adb_service.owned_server', return_value=True) as owner:
            self.s._recover(True)
            owner.assert_called_once_with('configured-adb')
            self.assertEqual(self.s.state['server_restarts'], 1)

    def test_private_server_recovery_checks_only_private_port(self):
        self.s.server_port = 5038
        with patch('adb_service.owned_server', return_value=True) as owner:
            self.s._recover(True)
            owner.assert_called_once_with('configured-adb', port=5038)

    def test_wrong_shell_output_is_not_success(self):
        self.s._command.return_value = reply('unrelated output')
        self.s._recover(False)
        self.assertFalse(self.s.state['device_ready'])

    def test_health_requires_two_host_failures_before_restart(self):
        self.s._command.return_value = {'error': 'timeout'}
        self.s._recover = Mock()
        self.s._health(); self.s._recover.assert_not_called()
        self.s._health(); self.s._recover.assert_called_once_with(host_hung=True)

    def test_healthy_host_with_hung_device_only_reconnects(self):
        self.s._command.side_effect = [reply('List of devices attached\n127.0.0.1:21503\tdevice'), {'error': 'timeout'}] * 2
        self.s._recover = Mock()
        self.s._health(); self.s._health()
        self.s._recover.assert_called_once_with(host_hung=False)
