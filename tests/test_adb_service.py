"""Fault injection against real worker/supervisor processes, without a device."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from adb_service import AdbService


class AdbServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = Path(self.temp.name) / 'fake_adb.py'
        self.fake.write_text('''import sys,time,os
from pathlib import Path
mode=sys.argv[1]
if mode=='sleep': time.sleep(float(sys.argv[2]))
elif mode=='effect':
 p=Path(sys.argv[2]);p.write_text(p.read_text()+'x' if p.exists() else 'x');time.sleep(5)
elif mode=='fail': sys.exit(7)
else: print('READY',flush=True)
''', encoding='utf-8')
        self.service = AdbService(sys.executable)
        self.addCleanup(self.service.close)

    def run_fake(self, mode='ok', *args, timeout=2):
        return self.service.run([sys.executable, str(self.fake), mode, *map(str, args)], timeout=timeout)

    def test_timeout_replaces_worker_and_next_command_succeeds(self):
        self.run_fake()
        before = self.service.status()['worker_pid']
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fake('sleep', 5, timeout=.25)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(self.run_fake().stdout.strip(), 'READY')
        after = self.service.status()
        self.assertNotEqual(before, after['worker_pid'])
        self.assertGreater(after['worker_restarts'], 0)

    def test_status_remains_responsive_while_command_blocks(self):
        self.run_fake()
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.run_fake, 'sleep', 1.5, timeout=3)
            time.sleep(.2)
            start = time.monotonic()
            self.assertIsNotNone(self.service.status()['supervisor_pid'])
            self.assertLess(time.monotonic() - start, .75)
            self.assertEqual(future.result(timeout=5).returncode, 0)

    def test_uncertain_mutation_is_not_replayed(self):
        marker = Path(self.temp.name) / 'effects'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fake('effect', marker, timeout=.5)
        self.run_fake()
        self.assertEqual(marker.read_text(), 'x')

    def test_nonzero_exit_is_preserved(self):
        self.assertEqual(self.run_fake('fail').returncode, 7)

    def test_expired_queued_mutation_never_runs_later(self):
        self.run_fake()
        marker = Path(self.temp.name) / 'effects'
        with ThreadPoolExecutor() as pool:
            long = pool.submit(self.run_fake, 'sleep', 7, timeout=8)
            time.sleep(.15)
            queued = pool.submit(self.run_fake, 'effect', marker, timeout=2)
            self.assertEqual(long.result(timeout=10).returncode, 0)
            with self.assertRaises(RuntimeError): queued.result(timeout=5)
        self.assertFalse(marker.exists())

    def test_executable_restricted_to_configured_program(self):
        with self.assertRaises(ValueError):
            self.service.run(['other-program', 'devices'])

    def test_flask_status_and_job_polling_stay_available_during_timeout(self):
        import app as web
        from unittest.mock import patch
        with patch.object(web.BypassManager, 'find_memu_dir', return_value=None):
            manager = web.BypassManager(command_executor=web.run_command)
        self.run_fake()
        client = web.app.test_client()
        with patch.object(web, 'manager', manager), patch.object(manager, 'start_frida_server',
                side_effect=lambda:self.run_fake('sleep', 5, timeout=.5)), patch.object(manager, 'refresh_status',
                side_effect=manager.status_snapshot):
            start = time.monotonic()
            accepted = client.post('/api/start_frida', json={})
            self.assertEqual(accepted.status_code, 202)
            self.assertLess(time.monotonic() - start, .2)
            job = accepted.json['job_id']
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                begin = time.monotonic()
                self.assertEqual(client.get('/api/status').status_code, 200)
                state = client.get('/api/jobs/' + job).json
                self.assertLess(time.monotonic() - begin, .2)
                if state['state'] == 'failed':break
                time.sleep(.05)
            self.assertEqual(state['state'], 'failed')
            self.assertEqual(self.run_fake().stdout.strip(), 'READY')

    def test_supervisor_death_does_not_replay_request(self):
        self.run_fake()
        self.service._process.kill()
        self.service._process.wait(timeout=3)
        # A new call starts a new supervisor; only this explicitly submitted call runs.
        self.assertEqual(self.run_fake().stdout.strip(), 'READY')

    def test_frozen_worker_is_replaced_by_external_deadline(self):
        try:
            import psutil
        except ImportError:
            self.skipTest('psutil needed only for test fault injection')
        self.run_fake()
        old = self.service.status()['worker_pid']
        worker = psutil.Process(old)
        worker.suspend()
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_fake(timeout=.25)
            self.assertEqual(self.run_fake().stdout.strip(), 'READY')
            self.assertNotEqual(old, self.service.status()['worker_pid'])
        finally:
            try: worker.resume()
            except psutil.NoSuchProcess: pass

    def test_frozen_worker_with_full_pipe_does_not_block_supervisor(self):
        try:
            import psutil
        except ImportError:
            self.skipTest('psutil needed only for test fault injection')
        self.run_fake()
        worker = psutil.Process(self.service.status()['worker_pid'])
        worker.suspend()
        try:
            start = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                self.service.run([sys.executable, str(self.fake), 'ok', 'x' * 64000], timeout=.25)
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(self.run_fake().stdout.strip(), 'READY')
        finally:
            try: worker.resume()
            except psutil.NoSuchProcess: pass
