"""Slow-network UI tests: real Chromium, controlled API delays, no device actions."""
import asyncio
import json
from pathlib import Path
import threading
import time
import unittest
from playwright.async_api import async_playwright, expect
from werkzeug.serving import make_server
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[1]

class LatencyUITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        ui_app=Flask('ui-latency-fixture',template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
        ui_app.add_url_rule('/',view_func=lambda:render_template('index.html'))
        cls.server=make_server('127.0.0.1',0,ui_app,threaded=True)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.worker.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.worker.join(timeout=2)

    async def asyncSetUp(self):
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch()
        self.page=await self.browser.new_page()
        self.errors=[];self.calls=[];self.active={};self.max_active={};self.delays={};self.jobs=False;self.job_fail=False;self.job_started=0;self.logcat_fail=False
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        await self.page.route('**/api/**',self.api)

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()
        self.assertEqual(self.errors,[])

    async def api(self,route):
        req=route.request;path=req.url.split('/api/')[1].split('?')[0]
        data=req.post_data_json if req.post_data else {}
        self.calls.append((path,data));self.active[path]=self.active.get(path,0)+1
        self.max_active[path]=max(self.max_active.get(path,0),self.active[path])
        try:
            await asyncio.sleep(self.delays.get(path,0))
            result={'success':True};status=200
            if path=='status': result.update(device_connected=True,frida_server_running=True,bypass_running=False,proxy_status='Kapatıldı',cert_status='not_installed',cert_installed=False)
            elif path=='list_packages':result['packages']=[{'package':'com.example.demo','name':'Demo'}]
            elif path=='list_scripts':result['scripts']=['nev1.js']
            elif path=='read_script':result['content']='// ready'
            elif path=='list_running_packages':result['packages']=[]
            elif path=='get_logs':result.update(logs=['ready'],revision=1)
            elif path=='get_logcat':
                result['logs']=[]
                if self.logcat_fail: result.update(success=False,message='Cihaz bağlı değil!')
            elif path=='check_devices' and self.jobs:
                self.job_started=time.monotonic();result.update(job_id='test-job',accepted=True);status=202
            elif path=='jobs/test-job':
                done=time.monotonic()-self.job_started>1.3
                result={'state':('failed' if self.job_fail else 'completed') if done else 'running','result':{'success':not self.job_fail,'error':'Test device timeout'} if done else None}
            await route.fulfill(json=result,status=status)
        finally:self.active[path]-=1

    async def ready(self):
        await self.page.goto(self.url,wait_until='domcontentloaded')
        await expect(self.page.locator('#script-editor')).to_have_value('// ready')

    async def test_slow_status_does_not_block_editor_or_logs(self):
        self.delays['status']=2.5
        start=time.perf_counter();await self.ready()
        await expect(self.page.locator('#console-output')).to_contain_text('ready')
        self.assertLess(time.perf_counter()-start,1.5)
        await self.page.locator('#dropdown-script .custom-select-trigger').click()
        await expect(self.page.locator('#dropdown-script .custom-select-dropdown')).to_be_visible()

    async def test_slow_polling_never_overlaps(self):
        self.delays.update(status=1.5,list_running_packages=1.5,get_logs=1.5)
        await self.ready();await asyncio.sleep(3.3)
        for path in self.delays:self.assertEqual(self.max_active[path],1,path)

    async def test_job_button_feedback_is_immediate_and_duplicate_click_is_blocked(self):
        self.jobs=True;await self.ready()
        button=self.page.locator('#btn-refresh')
        start=time.perf_counter();await button.click()
        await expect(button).to_have_attribute('aria-busy','true')
        self.assertLess(time.perf_counter()-start,.5)
        await button.evaluate('(button) => button.click()')
        await expect(button).not_to_have_attribute('aria-busy','true')
        self.assertEqual(len([name for name,_ in self.calls if name=='check_devices']),1)
        await expect(button).to_be_enabled()

    async def test_job_failure_restores_button_and_explains_error(self):
        self.jobs=True;self.job_fail=True;await self.ready()
        button=self.page.locator('#btn-refresh');await button.click()
        await expect(self.page.locator('#action-notice')).to_contain_text('Test device timeout')
        await expect(button).to_be_enabled()
        await expect(button).not_to_have_attribute('aria-busy','true')

    async def test_logcat_error_exits_loading_state(self):
        self.logcat_fail=True
        await self.ready();await self.page.locator('#btn-open-logcat').click()
        await expect(self.page.locator('#logcat-output')).to_contain_text('Cihaz bağlı değil!')
        self.logcat_fail=False
        await self.page.locator('#btn-refresh-logcat').click()
        await expect(self.page.locator('#logcat-output')).to_contain_text('Eşleşen log bulunamadı')

    async def test_logcat_typing_is_debounced(self):
        await self.ready();await self.page.locator('#btn-open-logcat').click()
        before=len([name for name,_ in self.calls if name=='get_logcat'])
        await self.page.locator('#logcat-text-filter').press_sequentially('abcdefghijkl',delay=20)
        await asyncio.sleep(.4)
        requests=[data for name,data in self.calls if name=='get_logcat']
        self.assertLessEqual(len(requests)-before,2)
        self.assertEqual(requests[-1]['filter_text'],'abcdefghijkl')

if __name__=='__main__':unittest.main()
