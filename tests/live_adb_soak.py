"""Opt-in read-only real-device endurance test through the running Flask broker."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
from local_api import panel_open
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--run', required=True)
parser.add_argument('--seconds', type=int, default=300)
args = parser.parse_args()
base = args.run.rstrip('/')
def request(path, data=None):
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base+path, data=payload, headers={'Content-Type':'application/json'} if payload else {})
    with panel_open(req, timeout=5) as response:
        return json.load(response)

initial = request('/api/status')
assert initial['device_connected'] and not initial['bypass_running']
report = dict(initial=initial, samples=[], javascript_errors=[])
commands = ['shell echo WEBMEMU_SOAK', 'shell ps -A', 'shell pm list packages -3', 'shell settings get global http_proxy']
started = time.monotonic()
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda err: report['javascript_errors'].append(str(err)))
    page.goto(base)
    while time.monotonic()-started < args.seconds:
        command = commands[len(report['samples']) % len(commands)]
        begin = time.monotonic()
        accepted = request('/api/run_adb', {'command':command})
        assert accepted.get('accepted'), accepted
        deadline = time.monotonic()+12
        while True:
            job = request('/api/jobs/'+accepted['job_id'])
            if job['state'] in ('completed','failed'):
                break
            assert time.monotonic() < deadline, job
            page.wait_for_timeout(30)
        state = request('/api/status')
        report['samples'].append(dict(command=command, ms=round((time.monotonic()-begin)*1000,2),
                                      job_state=job['state'], connected=state['device_connected'],
                                      service=state['adb_service']))
        if job['state'] != 'completed':
            report['failure'] = job
            break
        page.wait_for_timeout(200)
    report['final'] = request('/api/status')
    report['elapsed_seconds'] = round(time.monotonic()-started,2)
    report['horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.screenshot(path='artifacts/ui/private-adb-soak.png', full_page=True)
    browser.close()
Path('artifacts/performance/private-adb-soak.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
summary = dict(commands=len(report['samples']), seconds=report['elapsed_seconds'],
               failed=sum(s['job_state']!='completed' for s in report['samples']),
               max_ms=max(s['ms'] for s in report['samples']),
               worker_restarts=report['final']['adb_service']['worker_restarts']-initial['adb_service']['worker_restarts'],
               server_restarts=report['final']['adb_service']['server_restarts']-initial['adb_service']['server_restarts'],
               javascript_errors=report['javascript_errors'], final_connected=report['final']['device_connected'])
print(json.dumps(summary))
assert not summary['failed'] and not summary['worker_restarts'] and not summary['server_restarts'], summary
assert summary['final_connected'] and not summary['javascript_errors'] and not report['horizontal_overflow'], summary
