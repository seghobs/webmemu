import subprocess
import unittest
from unittest.mock import Mock, patch
import app as web


class AdbRecoveryTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            self.m = web.BypassManager(command_executor=web.run_command)
        self.m.adb = Mock(return_value=Mock(returncode=0, stdout='List of devices attached\n'))
        self.m.log = Mock()

    def test_healthy_host_uses_only_targeted_recovery(self):
        self.m.recover_transport = Mock(return_value=True)
        with patch.object(web, 'owned_server') as owner:
            self.assertTrue(self.m.recover_adb())
            owner.assert_not_called()

    def test_hung_host_uses_server_recovery_without_device_serial(self):
        self.m.adb.side_effect = subprocess.TimeoutExpired('devices', 2)
        self.m.recover_adb_server = Mock(return_value=True)
        self.assertTrue(self.m.recover_adb())
        self.m.recover_adb_server.assert_called_once()

    def test_stuck_shutdown_is_forced_only_after_ownership_recheck(self):
        def adb(*args, **kwargs):
            if args[0] == 'kill-server': raise subprocess.TimeoutExpired('kill', 2)
            return Mock(returncode=0, stdout='ok')
        self.m.adb.side_effect = adb
        with patch.object(web, 'owned_server', return_value=True) as owner:
            self.assertTrue(self.m.recover_adb_server())
            self.assertEqual(owner.call_count, 2)
            owner.assert_called_with(self.m.adb_path, terminate=True)
            self.assertFalse(self.m.recover_adb_server())

    def test_foreign_adb_is_never_stopped(self):
        with patch.object(web, 'owned_server', return_value=False):
            self.assertFalse(self.m.recover_adb_server())
            self.m.adb.assert_not_called()

    def test_active_session_job_or_multiple_devices_prevent_restart(self):
        with patch.object(web, 'owned_server') as owner:
            self.m.bypass_running = True
            self.assertFalse(self.m.recover_adb_server())
            self.m.bypass_running = False
            self.m._known_devices = ['a', 'b']
            self.assertFalse(self.m.recover_adb_server())
            self.m._known_devices = []
            with patch.object(self.m.jobs, 'running', return_value=True):
                self.assertFalse(self.m.recover_adb_server())
            owner.assert_not_called()

    def test_reconnect_restores_saved_tunnels_to_their_devices(self):
        self.m._known_devices = ['127.0.0.1:21503']
        self.m._saved_tunnels = [('127.0.0.1:21503', 'reverse', 'tcp:8080', 'tcp:8080')]
        with patch.object(web, 'owned_server', return_value=True), patch.object(web, 'run_command', return_value=Mock(returncode=0)) as run:
            self.assertTrue(self.m.recover_adb_server())
            self.m.adb.assert_any_call('connect', '127.0.0.1:21503', timeout=3)
            self.assertEqual(run.call_args.args[0][-5:], ['-s', '127.0.0.1:21503', 'reverse', 'tcp:8080', 'tcp:8080'])

    def test_failed_restart_does_not_report_success_and_releases_lock(self):
        self.m.adb.return_value.returncode = 1
        with patch.object(web, 'owned_server', return_value=True):
            self.assertFalse(self.m.recover_adb_server())
            self.assertTrue(self.m._recovery_lock.acquire(blocking=False))
            self.m._recovery_lock.release()

    def test_two_failed_probes_required(self):
        with patch.object(self.m, 'check_devices', side_effect=RuntimeError('unavailable')), patch.object(self.m, 'recover_adb', return_value=False) as recover:
            self.m.refresh_status(); recover.assert_not_called()
            self.m.refresh_status(); recover.assert_called_once()
