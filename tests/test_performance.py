import threading
import time
import unittest
from unittest.mock import Mock, patch
from runtime import SnapshotCache, JobRegistry
import app as web


def until(condition, timeout=2):
    deadline=time.monotonic()+timeout
    while not condition():
        if time.monotonic()>deadline: raise AssertionError('Background work did not finish')
        time.sleep(.005)


class CacheTests(unittest.TestCase):
    def test_slow_loader_never_blocks_requests_and_burst_is_coalesced(self):
        cache=SnapshotCache();gate=threading.Event();calls=[]
        def load(): calls.append(1);gate.wait(2);return {'ready':True}
        start=time.perf_counter()
        for _ in range(50): self.assertEqual(cache.get('status',load,1,{}),{})
        self.assertLess(time.perf_counter()-start,.1)
        until(lambda: len(calls)==1);self.assertEqual(len(calls),1)
        gate.set();until(lambda: 'status' not in cache.busy)
        self.assertEqual(cache.get('status',load,1,{}),{'ready':True})

    def test_invalidation_discards_results_started_before_action(self):
        cache=SnapshotCache();gate=threading.Event()
        cache.get('status',lambda: (gate.wait(2), 'old')[1],1,'empty')
        cache.invalidate();gate.set();until(lambda: not cache.busy)
        self.assertNotIn('status',cache.entries)
        cache.get('status',lambda:'new',1,'empty');until(lambda: not cache.busy)
        self.assertEqual(cache.get('status',lambda:'bad',1,'empty'),'new')

    def test_failed_loader_backs_off(self):
        cache=SnapshotCache();loader=Mock(side_effect=RuntimeError('timeout'))
        cache.get('x',loader,1,[]);until(lambda:not cache.busy)
        for _ in range(20): cache.get('x',loader,1,[])
        self.assertEqual(loader.call_count,1)
        self.assertIn('timeout',cache.errors['x'])

    def test_device_jobs_are_serialized_and_duplicates_share_job(self):
        jobs=JobRegistry();gate=threading.Event();events=[]
        def first(): events.append('first');gate.wait(2);return {'success':True}
        id1=jobs.submit('same',first)
        self.assertEqual(id1,jobs.submit('same',first))
        id2=jobs.submit('second',lambda: (events.append('second'), {'success':True})[1])
        until(lambda:events==['first']);self.assertEqual(jobs.get(id2)['state'],'queued')
        gate.set();until(lambda:jobs.get(id2)['state']=='completed')
        self.assertEqual(events,['first','second'])

    def test_job_failure_does_not_stall_queue(self):
        jobs=JobRegistry()
        id1=jobs.submit('fail',Mock(side_effect=RuntimeError('test failure')))
        id2=jobs.submit('ok',lambda:{'success':True})
        until(lambda:jobs.get(id2)['state']=='completed')
        self.assertEqual(jobs.get(id1)['state'],'failed')


class BackendPerformanceTests(unittest.TestCase):
    def setUp(self):
        with patch.object(web.BypassManager,'find_memu_dir',return_value=None): self.manager=web.BypassManager(command_executor=web.run_command)
        self.patch=patch.object(web,'manager',self.manager);self.patch.start();self.addCleanup(self.patch.stop)
        self.client=web.app.test_client()
        self.client.environ_base['HTTP_X_WEBMEMU_TOKEN'] = web.app.config['API_TOKEN']
        self.manager.device_connected=True

    def test_status_returns_while_device_probe_is_blocked(self):
        gate=threading.Event()
        with patch.object(self.manager,'refresh_status',side_effect=lambda:(gate.wait(2),self.manager.status_snapshot())[1]):
            start=time.perf_counter()
            for _ in range(10): self.assertEqual(self.client.get('/api/status').status_code,200)
            self.assertLess(time.perf_counter()-start,.2)
            gate.set();until(lambda:not self.manager.cache.busy)

    def test_slow_action_is_accepted_immediately_and_job_reports_failure(self):
        gate=threading.Event()
        def slow(): gate.wait(2);return False
        with patch.object(self.manager,'start_frida_server',side_effect=slow) as start:
            before=time.perf_counter()
            response=self.client.post('/api/start_frida',json={})
            self.assertEqual(response.status_code,202)
            self.assertLess(time.perf_counter()-before,.2)
            duplicate=self.client.post('/api/start_frida',json={})
            self.assertEqual(response.json['job_id'],duplicate.json['job_id'])
            gate.set()
            until(lambda:self.manager.jobs.get(response.json['job_id'])['state']=='failed')
            self.assertFalse(self.client.get('/api/jobs/'+response.json['job_id']).json['result']['success'])
            self.assertEqual(start.call_count,1)

    def test_connected_device_skips_port_discovery(self):
        with patch.object(self.manager,'adb',return_value=Mock(returncode=0,stdout='List of devices attached\n127.0.0.1:21503\tdevice\n')) as adb, patch.object(web.socket,'create_connection') as connect:
            self.assertTrue(self.manager.check_devices(details=False))
            self.assertEqual(adb.call_count,1);connect.assert_not_called()
            self.assertEqual(self.manager.device_serial,'127.0.0.1:21503')

    def test_connecting_invalidates_empty_package_snapshot(self):
        self.manager.device_connected=False
        self.manager.cache.entries['packages-True']=(time.monotonic(),[])
        with patch.object(self.manager,'adb',return_value=Mock(returncode=0,stdout='List of devices attached\n127.0.0.1:21503\tdevice\n')):
            self.manager.check_devices(details=False)
        self.assertEqual(self.manager.cache.entries['packages-True'][0],0)

    def test_frida_probe_matches_process_name_not_shell_text(self):
        with patch.object(self.manager,'adb',return_value=Mock(returncode=0,stdout='USER PID NAME\nshell 2 grep-frida\n')):
            self.assertFalse(self.manager.check_frida_status())
        with patch.object(self.manager,'adb',return_value=Mock(returncode=0,stdout='USER PID NAME\nroot 2 frida-server\n')):
            self.assertTrue(self.manager.check_frida_status())

    def test_proxy_batches_and_verifies_settings(self):
        with patch.object(self.manager,'adb',return_value=Mock(returncode=0,stdout='127.0.0.1:8080\n')) as adb, patch.object(web.time,'sleep') as sleep:
            self.assertTrue(self.manager.set_proxy())
            self.assertEqual(adb.call_count,2);sleep.assert_not_called()
            self.assertIn('&& settings get global http_proxy',adb.call_args.args[1])
        with patch.object(self.manager,'adb',return_value=Mock(returncode=1,stdout='',stderr='denied')):
            with self.assertRaises(RuntimeError): self.manager.set_proxy()

    def test_transport_recovery_is_targeted_and_preserves_tunnels(self):
        self.manager.device_serial='127.0.0.1:21503'
        results=[Mock(stdout='host-1 tcp:8080 tcp:8080',returncode=0),Mock(stdout='127.0.0.1:21503 tcp:13337 tcp:13337',returncode=0)]
        def adb(*args, **kwargs):
            if args[:2] == ('reverse','--list'): return results[0]
            if args[:2] == ('forward','--list'): return results[1]
            if args == ('shell', 'echo WEBMEMU_READY'): return Mock(returncode=0, stdout='WEBMEMU_READY\n')
            return Mock(returncode=0,stdout='connected')
        with patch.object(self.manager,'adb',side_effect=adb) as command:
            self.assertTrue(self.manager.recover_transport())
            calls=[call.args for call in command.call_args_list]
            self.assertIn(('disconnect','127.0.0.1:21503'),calls)
            self.assertIn(('reverse','tcp:8080','tcp:8080'),calls)
            self.assertIn(('forward','tcp:13337','tcp:13337'),calls)
            self.assertFalse(self.manager.recover_transport())
            self.assertFalse(any('kill-server' in args for args in calls))

    def test_transport_recovery_does_not_interrupt_active_session(self):
        self.manager.device_serial='127.0.0.1:21503'
        self.manager.bypass_running=True
        with patch.object(self.manager,'adb') as adb:
            self.assertFalse(self.manager.recover_transport());adb.assert_not_called()

    def test_reconnect_without_shell_response_is_not_recovery(self):
        self.manager.device_serial = '127.0.0.1:21503'
        with patch.object(self.manager, 'adb', return_value=Mock(returncode=0, stdout='')):
            self.assertFalse(self.manager.recover_transport())

    def test_adb_calls_do_not_overlap(self):
        gate = threading.Event(); entered = threading.Event(); calls = []
        def run(*args, **kwargs):
            calls.append(args); entered.set(); gate.wait(2)
            return Mock(returncode=0, stdout='ok')
        with patch.object(self.manager, '_command_executor', side_effect=run):
            first = threading.Thread(target=lambda:self.manager.adb('shell', 'echo first'))
            second = threading.Thread(target=lambda:self.manager.adb('shell', 'echo second'))
            first.start(); self.assertTrue(entered.wait(1)); second.start()
            try:
                time.sleep(.05); self.assertEqual(len(calls), 1)
            finally:
                gate.set(); first.join(2); second.join(2)
            self.assertEqual(len(calls), 2)

    def test_package_timeout_is_not_an_empty_success(self):
        with patch.object(self.manager, 'adb', side_effect=TimeoutError('device unavailable')):
            with self.assertRaises(RuntimeError): self.manager.list_packages()

    def test_logcat_matches_numeric_pid_and_child_process(self):
        self.manager.cache.entries['process_table']=(time.monotonic(),{'com.example.demo':['123'],'com.example.demo:worker':['456']})
        rows=['09-15 12:30:01.000   123  200 I Demo: ready','09-15 12:30:02.000   456  201 E Worker: failed','09-15 12:30:03.000   999  202 I Other: ignore']
        self.manager.cache.entries['logcat']=(time.monotonic(),rows)
        _,_,logs=self.manager.get_app_logcat('com.example.demo','',100)
        self.assertEqual(logs,rows[:2])
        _,_,logs=self.manager.get_app_logcat('com.example.demo','failed',100)
        self.assertEqual(logs,[rows[1]])

    def test_logcat_rejects_unbounded_and_invalid_counts(self):
        for lines in [0,-1,1001,'bad',None]:
            self.assertEqual(self.client.post('/api/get_logcat',json={'lines':lines}).status_code,400)

if __name__=='__main__':unittest.main()
