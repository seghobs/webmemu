"""Opt-in live stop/start regression: python tests/live_frida_start.py --run URL.

Requires an idle test emulator. Restarts Frida three times, leaves it running,
and preserves the UI's existing proxy settings. Does not inject any scripts.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
if len(sys.argv) != 3 or sys.argv[1] != '--run':
    raise SystemExit('Explicit --run URL is required: this test restarts Frida.')
BASE = sys.argv[2].rstrip('/')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=4) as response:
        return json.load(response)


def wait_job(job_id):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        job = get('/api/jobs/' + job_id)
        if job['state'] in ('completed', 'failed'):
            return job
        time.sleep(.05)
    raise AssertionError(f'Job did not finish: {job_id}')


report = {'url': BASE, 'cold_starts': [], 'javascript_errors': []}
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1600, 'height': 1000})
    page.on('pageerror', lambda error: report['javascript_errors'].append(str(error)))
    page.goto(BASE)
    start_button = page.locator('#btn-start-frida')
    stop_button = page.locator('#btn-stop-frida')
    expect(stop_button).to_be_enabled(timeout=12000)
    for cycle in range(3):
        assert not get('/api/status')['bypass_running'], 'Active session; refusing restart'
        with page.expect_response('**/api/stop_frida') as stopped:
            stop_button.click()
        stop_job = stopped.value.json()['job_id']
        expect(start_button).to_be_enabled(timeout=12000)
        assert wait_job(stop_job)['state'] == 'completed'
        assert not get('/api/status')['frida_server_running']
        started = time.perf_counter()
        with page.expect_response('**/api/start_frida') as launched:
            start_button.click()
        launch_job = launched.value.json()['job_id']
        accepted_ms = (time.perf_counter() - started) * 1000
        expect(stop_button).to_be_enabled(timeout=12000)
        expect(start_button).not_to_have_attribute('aria-busy', 'true', timeout=12000)
        job = wait_job(launch_job)
        assert job['state'] == 'completed', job
        assert job['result']['success'], job
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Protocol-level verification is independent of the panel's ps check.
        probe = subprocess.run(
            [sys.executable, '-c', 'import frida,sys; print(len(frida.get_device_manager().add_remote_device(sys.argv[1]).enumerate_processes()))',
             get('/api/status')['frida_endpoint']],
            capture_output=True, text=True, timeout=6,
        )
        assert probe.returncode == 0 and int(probe.stdout.strip()) > 0, probe.stderr
        report['cold_starts'].append({'cycle': cycle + 1, 'click_to_accept_ms': round(accepted_ms, 2),
                                     'ui_complete_ms': round(elapsed_ms, 2), 'job': job,
                                     'frida_protocol_process_count': int(probe.stdout.strip())})
    report['horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth > innerWidth')
    assert not report['horizontal_overflow']
    assert not report['javascript_errors']
    page.screenshot(path=str(ROOT / 'artifacts/ui/frida-cold-start.png'), full_page=True)
    browser.close()
(ROOT / 'artifacts/performance/frida-cold-start.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=True, indent=2))
