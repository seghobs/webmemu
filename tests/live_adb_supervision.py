"""Opt-in fault test: freezes only the panel's own idle ADB worker process."""
import json
from pathlib import Path
import sys
import time
import urllib.request
from local_api import panel_open
import psutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) not in (3, 4) or sys.argv[1] != '--freeze-worker':
    raise SystemExit('Use --freeze-worker URL; this deliberately suspends an idle worker.')
BASE = sys.argv[2].rstrip('/')


def status():
    start = time.perf_counter()
    with panel_open(BASE + '/api/status', timeout=3) as response:
        data = json.load(response)
    return data, round((time.perf_counter() - start) * 1000, 2)


initial, _ = status()
assert not initial['bypass_running'], 'Active analysis session'
connected_test = len(sys.argv) == 4 and sys.argv[3] == '--connected-device'
assert not initial['device_connected'] or connected_test, 'Use --connected-device explicitly for a connected device'
expected_serial = initial.get('device_serial') if initial['device_connected'] else None
backend = initial['adb_service']
assert not backend.get('active'), 'Active device operation'
old_pid = backend['worker_pid']
worker = psutil.Process(old_pid)
assert worker.ppid() == backend['supervisor_pid'], 'Worker identity changed'
assert 'multiprocessing' in ' '.join(worker.cmdline()), 'Not the expected worker'
report = {'url': BASE, 'initial': backend, 'samples_ms': [], 'javascript_errors': []}
started = time.monotonic()
worker.suspend()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda err: report['javascript_errors'].append(str(err)))
        page.goto(BASE, wait_until='domcontentloaded')
        deadline = started + 25
        while time.monotonic() < deadline:
            current, ms = status()
            report['samples_ms'].append(ms)
            observed = current.get('adb_service', {})
            if observed.get('worker_pid') != old_pid and observed.get('worker_restarts', 0) > backend.get('worker_restarts', 0):
                report['after'] = observed
                report['recovery_ms'] = round((time.monotonic() - started) * 1000, 2)
                break
            page.wait_for_timeout(250)
        assert 'after' in report, 'Frozen worker was not replaced'
        while time.monotonic() < deadline:
            current, ms = status()
            report['samples_ms'].append(ms)
            observed = current.get('adb_service', {})
            if (observed.get('worker_pid') != old_pid and observed.get('last_error') is None
                    and (not expected_serial or (observed.get('device_ready') and current.get('device_connected')
                         and current.get('device_serial') == expected_serial))):
                report['host_healthy_after'] = observed
                break
            page.wait_for_timeout(250)
        assert 'host_healthy_after' in report, 'New worker did not restore host health'
        assert max(report['samples_ms']) < 400, 'Flask became unresponsive'
        assert report['javascript_errors'] == []
        report['horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not report['horizontal_overflow']
        page.screenshot(path=str(ROOT / 'artifacts/ui/supervised-adb.png'), full_page=True)
        browser.close()
finally:
    try: worker.resume()
    except psutil.NoSuchProcess: pass
(ROOT / 'artifacts/performance/live-supervision.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=True, indent=2))
