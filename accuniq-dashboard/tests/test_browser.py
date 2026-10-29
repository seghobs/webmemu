"""Real Next.js + FastAPI browser tests against a temporary SQLite database.

Starts only its own loopback servers and terminates only those process handles.
Never accesses the ACCUNIQ device or cloud service.
"""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-output'
OUT.mkdir(exist_ok=True)
SAMPLE=(ROOT/'backend/tests/fixtures/apk-sample.txt').read_text()
env=dict(os.environ,ACCUNIQ_DB_PATH=str(OUT/'ui-test.sqlite3'),BACKEND_URL='http://127.0.0.1:8010',NEXT_TELEMETRY_DISABLED='1')
processes=[]
logs=[]
report={'checks':[],'page_errors':[],'console_errors':[],'screenshots':[]}

def start(args,cwd,name):
    log=(OUT/name).open('w',encoding='utf-8');logs.append(log)
    process=subprocess.Popen(args,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    processes.append(process)
    return process
def ready(url):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if any(p.poll() is not None for p in processes):raise RuntimeError('Test server exited; inspect test-output logs')
        try:
            with urllib.request.urlopen(url,timeout=2) as response:
                if response.status==200:return
        except Exception:time.sleep(.3)
    raise RuntimeError('Test server did not become ready')
def check(label):report['checks'].append(label)
def overflow(page):
    if page.evaluate('document.documentElement.scrollWidth > innerWidth'):
        page.screenshot(path=str(OUT/'overflow-debug.png'),full_page=True)
        bad=page.evaluate('({viewport:innerWidth,document:document.documentElement.scrollWidth,elements:Array.from(document.querySelectorAll("*" )).filter(e=>!e.closest(".table-wrap") && e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right,position:getComputedStyle(e).position}))})')
        raise AssertionError('Horizontal page overflow: '+json.dumps(bad))
def screenshot(page,name):
    page.screenshot(path=str(OUT/name),full_page=True);report['screenshots'].append(name)

try:
    # Never point test traffic at an already running user workspace.
    for port in (8010,3002):
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',port))
            except OSError as exc:raise RuntimeError(f'{port} portu dolu; test kendi sunucusunu başlatmalıdır.') from exc
    for suffix in ('','-wal','-shm'):
        target=OUT/('ui-test.sqlite3'+suffix)
        if target.exists():target.unlink()
    start([sys.executable,'-B','-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8010'],ROOT,'ui-backend.log')
    ready('http://127.0.0.1:8010/api/health')
    start(['node',str(ROOT/'frontend/node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','3002'],ROOT/'frontend','ui-frontend.log')
    ready('http://127.0.0.1:3002')
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        page.on('console',lambda m:report['console_errors'].append(m.text) if m.type=='error' else None)
        page.goto('http://127.0.0.1:3002')
        expect(page.get_by_text('Önce bir profil, sonra ilk ölçüm.')).to_be_visible()
        overflow(page);screenshot(page,'overview-empty-desktop.png');check('Empty workspace and no automatic demo data')
        page.get_by_role('button',name='Profil ekle',exact=True).click()
        page.get_by_label('Profil adı').fill('Deniz')
        page.get_by_role('button',name='Profil oluştur',exact=True).click()
        expect(page.get_by_label('Aktif profil')).to_have_value(__import__('re').compile('.+'))
        check('Create profile through real backend')
        page.get_by_role('button',name='Ölçüm ekle',exact=True).click()
        page.get_by_label('QR verisi').fill('geçersiz veri')
        page.get_by_role('button',name='Ölçümü önizle',exact=True).click()
        expect(page.locator('.modal [role="alert"]')).to_contain_text('527 karakter')
        expect(page.get_by_role('button',name='Ölçümü kaydet',exact=True)).to_have_count(0)
        check('Invalid QR rejected without recording')
        page.get_by_label('QR verisi').fill('https://qr.accuniq.com/accuniq?bcadata='+SAMPLE)
        page.get_by_role('button',name='Ölçümü önizle',exact=True).click()
        expect(page.get_by_text('QR verisi çözümlendi')).to_be_visible()
        expect(page.locator('.preview-metrics')).to_contain_text('70,0')
        page.get_by_role('button',name='Ölçümü kaydet',exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('.metric-value').first).to_contain_text('70,0')
        screenshot(page,'overview-qr-desktop.png');check('QR URL preview and persisted metrics match APK sample')
        page.get_by_role('button',name='Ölçüm ekle',exact=True).click()
        page.get_by_label('QR verisi').fill(SAMPLE)
        page.get_by_role('button',name='Ölçümü önizle',exact=True).click()
        expect(page.get_by_text('Bu QR zaten kayıtlı.',exact=False)).to_be_visible()
        page.get_by_role('button',name='Ölçümü kaydet',exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('.recent tbody tr')).to_have_count(1)
        check('Duplicate import does not create a second record')
        page.get_by_role('button',name='Ölçüm ekle',exact=True).click()
        page.get_by_role('button',name='Görsel',exact=True).click()
        with page.expect_response('**/api/qr/scanner/preview') as scan_response:
            page.locator('input[type="file"]').set_input_files(str(ROOT/'backend/tests/fixtures/apk-sample-qr.png'))
        assert scan_response.value.status==200
        expect(page.get_by_text('QR verisi çözümlendi')).to_be_visible()
        expect(page.locator('.preview-metrics')).to_contain_text('70,0')
        with page.expect_response('**/api/qr/scanner/import') as import_response:
            page.get_by_role('button',name='Ölçümü kaydet',exact=True).click()
        assert import_response.value.status==200
        assert import_response.value.json()['duplicate']
        expect(page.get_by_role('dialog')).to_have_count(0)
        check('Image QR uses native scanner contract and duplicate protection')
        page.get_by_role('button',name='Ölçüm geçmişi',exact=True).click()
        expect(page.get_by_text('Ölçüm arşivi')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('link',name='CSV indir',exact=True).click()
        assert download.value.suggested_filename=='accuniq-measurements.csv'
        check('CSV export is downloadable')
        page.get_by_role('button',name='28 Şubat 2018 ölçümünü aç').click()
        expect(page.locator('.detail-metrics')).to_contain_text('70,0')
        page.get_by_text('Ham QR alanlarını göster').click()
        expect(page.locator('.raw-fields')).to_contain_text('IsKgLb')
        page.keyboard.press('Escape');expect(page.get_by_role('dialog')).to_have_count(0)
        check('Measurement detail, raw fields and keyboard dismissal')
        page.get_by_role('button',name='Cihaz & aktarım',exact=True).click()
        expect(page.get_by_text('QR ile ölçüm aktarımı')).to_be_visible()
        expect(page.get_by_role('button',name='Cihaz doğrulaması gerekli')).to_have_count(2)
        check('USB and Bluetooth clearly pending, QR ready')
        expect(page.get_by_role('heading',name='Native uygulama analizi')).to_be_visible()
        expect(page.locator('.dex-stats')).to_contain_text('19.257')
        page.get_by_text('Kaynak kanıtlarını göster',exact=True).click()
        expect(page.locator('.dex-evidence')).to_contain_text('SCAN_RESULT')
        with page.expect_download() as download:
            page.get_by_role('link',name='DEX raporunu indir').click()
        assert download.value.suggested_filename=='DEX-ANALIZ-RAPORU.md'
        overflow(page);screenshot(page,'dex-analysis-desktop.png')
        check('DEX evidence panel and downloadable report')
        page.get_by_role('button',name='API & veri haritası',exact=True).click()
        expect(page.locator('.reference-panel tbody tr')).to_have_count(20)
        page.get_by_label('API tanımlarında ara').fill('bodyComposition')
        expect(page.locator('.reference-panel tbody tr')).to_have_count(3)
        page.get_by_role('button',name='QR alanları',exact=False).click()
        expect(page.locator('.reference-panel tbody tr')).to_have_count(145)
        check('Searchable API inventory and QR map loaded from FastAPI')
        page.get_by_role('button',name='Yerel veriler',exact=True).click()
        page.get_by_role('button',name='Örnek veriyi göster',exact=True).click()
        expect(page.locator('.context-right')).to_contain_text('Örnek veri')
        expect(page.locator('.recent tbody tr')).to_have_count(5)
        expect(page.get_by_role('img',name='Ölçüm trendi')).to_be_visible()
        overflow(page);screenshot(page,'overview-demo-desktop.png');check('Explicit demo profile and trend chart')
        for width in (390,768):
            page.set_viewport_size({'width':width,'height':900})
            overflow(page);screenshot(page,f'overview-demo-{width}.png')
            for menu in ('Ölçüm geçmişi','Profiller','Cihaz & aktarım','API & veri haritası','Yerel veriler','Genel bakış'):
                page.get_by_role('button',name=menu,exact=True).click()
                page.wait_for_timeout(120)
                overflow(page)
            check(f'All navigation views fit {width}px viewport')
        page.set_viewport_size({'width':390,'height':900})
        page.get_by_role('button',name='Ölçüm ekle',exact=True).click()
        overflow(page);screenshot(page,'import-mobile.png')
        page.get_by_role('button',name='Kamera',exact=True).click()
        page.get_by_role('button',name='Kamerayı aç',exact=True).click()
        expect(page.get_by_text('Kamera açılamadı.',exact=False)).to_be_visible(timeout=15000)
        page.keyboard.press('Escape');check('Camera denial is handled without crash')
        # A controlled network failure, separate from normal-flow console errors.
        normal_errors=list(report['console_errors'])
        page.route('**/api/patients',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Test bağlantı kesintisi"}'))
        page.reload();expect(page.get_by_text('Backend çevrimdışı')).to_be_visible()
        expect(page.get_by_role('button',name='Ölçüm ekle',exact=True)).to_be_disabled()
        page.unroute('**/api/patients')
        page.get_by_role('button',name='Yeniden dene').click()
        expect(page.get_by_text('Yerel sunucu',exact=True)).to_be_visible()
        check('Backend outage and retry recover cleanly')
        report['console_errors']=normal_errors
        assert not report['page_errors'],report['page_errors']
        # 422 is the explicitly tested malformed QR response, not a JS error.
        report['console_errors']=[m for m in report['console_errors'] if '422 (Unprocessable Entity)' not in m]
        assert not report['console_errors'],report['console_errors']
        browser.close()
    report['passed']=True
finally:
    for process in reversed(processes):
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    for log in logs:log.close()
    (OUT/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
