"""Read-only live benchmark, plus the non-destructive device refresh action."""
import json
import sys
import atexit
import threading
import statistics
import time
import urllib.request
from local_api import panel_open
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:5000'
if '--isolated' in sys.argv:
    sys.path.insert(0, str(ROOT))
    from app import app
    from werkzeug.serving import make_server
    server=make_server('127.0.0.1',0,app,threaded=True)
    server_thread=threading.Thread(target=server.serve_forever,daemon=True)
    server_thread.start()
    BASE=f'http://127.0.0.1:{server.server_port}'
    atexit.register(server.shutdown)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path,body=None):
    start=time.perf_counter()
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {})
    with panel_open(req,timeout=10) as res:data=json.load(res);status=res.status
    return data,round((time.perf_counter()-start)*1000,2),status

result={'api':{},'ui':{},'javascript_errors':[]}
status,_,_=request('/api/status')
assert status.get('api_version')==2,'The live server has not loaded the new code'
for _ in range(20):
    status,_,_=request('/api/status')
    if status.get('device_connected') and not status.get('error'):break
    time.sleep(.3)
assert status.get('device_connected') and not status.get('error'), 'Device not ready'
for endpoint in ['status','list_running_packages','list_packages','get_logs']:
    values=[request('/api/'+endpoint)[1] for _ in range(5)]
    result['api'][endpoint]={'samples_ms':values,'median_ms':round(statistics.median(values),2),'max_ms':max(values)}
job,accept_ms,_=request('/api/check_devices',{})
result['api']['check_devices']={'accept_ms':accept_ms}
started=time.perf_counter()
while True:
    state,_,_=request('/api/jobs/'+job['job_id'])
    if state['state'] in ('completed','failed'):break
    time.sleep(.05)
result['api']['check_devices'].update(completion_ms=round((time.perf_counter()-started)*1000,2),state=state['state'])
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:result['javascript_errors'].append(str(error)))
    started=time.perf_counter();page.goto(BASE,wait_until='domcontentloaded')
    expect(page.locator('#script-editor')).not_to_have_value('',timeout=8000)
    result['ui']['editor_ready_ms']=round((time.perf_counter()-started)*1000,2)
    started=time.perf_counter();page.locator('#dropdown-script .custom-select-trigger').click()
    expect(page.locator('#dropdown-script .custom-select-dropdown')).to_be_visible()
    result['ui']['dropdown_open_ms']=round((time.perf_counter()-started)*1000,2)
    page.keyboard.press('Escape')
    page.evaluate('''() => {
        const button=document.querySelector('#btn-refresh');
        button.addEventListener('click',()=>{window.clickAt=performance.now();},true);
        new MutationObserver(()=>{if(button.getAttribute('aria-busy')==='true')window.feedbackMs=performance.now()-window.clickAt;}).observe(button,{attributes:true,attributeFilter:['aria-busy']});
    }''')
    started=time.perf_counter();page.locator('#btn-refresh').click()
    expect(page.locator('#btn-refresh')).not_to_have_attribute('aria-busy','true',timeout=15000)
    result['ui']['refresh_complete_ms']=round((time.perf_counter()-started)*1000,2)
    result['ui']['click_feedback_ms']=page.evaluate('window.feedbackMs')
    assert result['ui']['click_feedback_ms'] is not None
    page.locator('#btn-open-logcat').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.locator('#logcat-text-filter').fill('__performance_probe_no_match__')
    expect(page.locator('#logcat-output')).to_contain_text('Eşleşen log',timeout=8000)
    page.keyboard.press('Escape')
    result['ui']['horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.wait_for_timeout(2000)
    browser.close()
(ROOT/'artifacts/performance/after.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
assert result['api']['check_devices']['state']=='completed'
assert not result['javascript_errors']
