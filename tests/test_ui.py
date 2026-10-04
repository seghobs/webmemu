"""Browser regression suite. All device APIs are intercepted; no ADB/Frida actions run."""
import json
from pathlib import Path
import threading
import unittest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts' / 'ui'

class StudioUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_app = Flask('ui-fixture', template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))
        ui_app.add_url_rule('/', view_func=lambda: render_template('index.html'))
        cls.server = make_server('127.0.0.1', 0, ui_app, threaded=True)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.worker.join(timeout=3)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
        self.page = self.context.new_page()
        self.errors, self.calls = [], []
        self.status = {'device_connected':True, 'frida_server_running':True, 'bypass_running':False, 'proxy_status':'127.0.0.1:8080', 'cert_status':'installed', 'cert_installed':True, 'stealth_mode':False}
        self.revision = 101
        self.logs = [f'[12:30:{i%60:02}] Test log {i}' for i in range(100)]
        self.fail_status = False
        self.fail_save = False
        self.script = '// UI test fixture\nJava.perform(function () {\n    console.log("Ready");\n});'
        self.page.on('pageerror', lambda err: self.errors.append(str(err)))
        self.page.on('console', lambda msg: self.errors.append(msg.text) if msg.type=='error' and not (self.fail_status and '503' in msg.text) else None)
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.page.route('**/api/**', self.api)
        self.page.goto(self.url)
        expect(self.page.locator('#script-editor')).to_have_value(self.script)
        expect(self.page.locator('#val-device')).to_have_text('Bağlı')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Browser errors')

    def api(self, route):
        req = route.request
        path = req.url.split('/api/')[1].split('?')[0]
        data = req.post_data_json if req.post_data and 'application/json' in req.headers.get('content-type','') else None
        self.calls.append((path,data))
        response = {'success':True}
        if path == 'status':
            if self.fail_status:
                route.fulfill(json={'error':'offline'}, status=503); return
            response = self.status
        elif path == 'list_packages': response['packages']=[{'package':'com.example.demo','name':'Demo uygulaması'},{'package':'com.example.browser','name':'Test tarayıcı'}]
        elif path == 'list_scripts': response['scripts']=['nev1.js','headers.js']
        elif path == 'read_script': response['content']=self.script
        elif path == 'save_script':
            if self.fail_save: response={'success':False,'error':'Disk yazma hatası'}
            else: self.script=data['content']
        elif path == 'list_running_packages': response['packages']=['com.example.demo','com.example.browser']
        elif path == 'get_logs': response.update(logs=self.logs, revision=self.revision)
        elif path == 'get_logcat': response['logs']=['09-15 12:30:00 I Demo: Ready','09-15 12:30:01 E Demo: Test error'] if not data.get('filter_text') else []
        elif path == 'clear_logs': self.logs=[];self.revision+=1
        elif path == 'start_bypass': self.status['bypass_running']=True
        elif path == 'stop_bypass': self.status['bypass_running']=False
        elif path == 'log_frontend_error': self.errors.append(str(data))
        route.fulfill(json=response)

    def assert_no_overflow(self):
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal document overflow')
        for selector in ['.top-config-row','.status-panel','#editor-pane','#console-pane']:
            box=self.page.locator(selector).bounding_box()
            self.assertGreaterEqual(box['x'],0)
            self.assertLessEqual(box['x']+box['width'],self.page.viewport_size['width']+1)

    def test_responsive_layouts_and_screenshots(self):
        for width,height in [(1920,1080),(1440,1000),(1024,768),(768,1024),(390,844),(320,740)]:
            self.page.set_viewport_size({'width':width,'height':height})
            self.assert_no_overflow()
            self.page.locator('summary').click()
            expect(self.page.locator('#select-bypass-mode')).to_be_visible()
            self.assert_no_overflow()
            self.page.locator('summary').click()
            if width in (1440,390):
                self.page.screenshot(path=str(ARTIFACTS / f'studio-{width}.png'), full_page=True)

    def test_dropdown_keyboard_search_and_selection(self):
        trigger=self.page.locator('#dropdown-app .custom-select-trigger')
        trigger.click()
        self.page.get_by_role('textbox',name='Uygulama ara',exact=True).fill('tarayıcı')
        self.page.keyboard.press('ArrowDown')
        self.page.keyboard.press('Enter')
        expect(trigger).to_contain_text('Test tarayıcı')
        expect(trigger).to_have_attribute('aria-expanded','false')
        trigger.click()
        self.page.keyboard.press('Escape')
        expect(trigger).to_be_focused()
        self.page.locator('#dropdown-script .custom-select-trigger').click()
        self.page.locator('#dropdown-script .custom-select-option').filter(has_text='headers.js').click()
        expect(self.page.locator('#editor-filename')).to_have_text('headers.js')

    def test_save_dirty_state_and_server_error(self):
        editor=self.page.locator('#script-editor')
        editor.fill('// modified')
        expect(self.page.locator('#editor-save-status')).to_have_text('Kaydedilmedi')
        editor.press('Control+s')
        expect(self.page.locator('#editor-save-status')).to_have_text('Kaydedildi')
        self.assertIn(('save_script',{'script_name':'nev1.js','content':'// modified'}),self.calls)
        self.fail_save=True
        editor.fill('// second edit')
        self.page.locator('#btn-save-script').click()
        expect(self.page.locator('#action-notice')).to_contain_text('Disk yazma hatası')
        expect(self.page.locator('#editor-save-status')).to_have_text('Kaydedilmedi')
        expect(self.page.locator('#btn-save-script')).to_be_enabled()

    def test_log_updates_after_100_rows(self):
        expect(self.page.locator('#console-output .log-entry')).to_have_count(100)
        self.logs=self.logs[1:]+['New log after capacity'];self.revision+=1
        expect(self.page.locator('#console-output')).to_contain_text('New log after capacity')
        self.page.locator('#btn-clear-console').click()
        expect(self.page.locator('#console-output')).not_to_contain_text('New log after capacity')

    def test_logcat_modal_filters_keyboard_and_mobile(self):
        for width in (1440,390):
            self.page.set_viewport_size({'width':width,'height':900})
            self.page.locator('#btn-open-logcat').click()
            expect(self.page.get_by_role('dialog')).to_be_visible()
            expect(self.page.locator('#btn-close-logcat')).to_be_focused()
            expect(self.page.locator('#logcat-output')).to_contain_text('Demo: Ready')
            self.page.locator('#logcat-text-filter').fill('no match')
            expect(self.page.locator('#logcat-output')).to_contain_text('Eşleşen log bulunamadı')
            self.assert_no_overflow()
            self.page.screenshot(path=str(ARTIFACTS / f'logcat-{width}.png'),full_page=False)
            self.page.keyboard.press('Escape')
            expect(self.page.get_by_role('dialog')).not_to_be_visible()
            expect(self.page.locator('#btn-open-logcat')).to_be_focused()
            self.page.locator('#logcat-text-filter').evaluate('(el) => el.value = ""')

    def test_server_unavailable_and_recovery(self):
        self.fail_status = True
        expect(self.page.locator('#session-status')).to_have_text('Sunucuya ulaşılamıyor')
        expect(self.page.locator('#btn-start-bypass')).to_be_disabled()
        self.fail_status = False
        expect(self.page.locator('#session-status')).to_contain_text('Cihaz hazır')
        expect(self.page.locator('#btn-start-bypass')).to_be_enabled()

    def test_sidebar_actions_and_adb_input(self):
        self.page.locator('#proxy-ip').fill('127.0.0.1')
        self.page.locator('#proxy-port').fill('8888')
        self.page.locator('#btn-proxy-on').click()
        self.page.locator('#btn-proxy-off').click()
        self.page.locator('#btn-cert-remove').click()
        self.page.locator('#btn-start-memu').click()
        self.page.locator('#btn-start-mitmweb').click()
        self.page.locator('#adb-cmd-input').fill('shell echo ready')
        self.page.locator('#adb-cmd-input').press('Enter')
        self.page.locator('#btn-quick-restart').click()
        self.assertIn(('toggle_proxy', {'enabled':True,'proxy_ip':'127.0.0.1','proxy_port':'8888'}),self.calls)
        self.assertIn(('toggle_proxy', {'enabled':False}),self.calls)
        self.assertIn(('run_adb', {'command':'shell echo ready'}),self.calls)
        for action in ['remove_cert','start_memu','start_mitmweb','quick_action/restart_app']:
            self.assertIn(action,[name for name,_ in self.calls])
        ids=self.page.locator('[id]').evaluate_all('(els) => els.map(el => el.id)')
        self.assertEqual(len(ids),len(set(ids)), 'Duplicate element IDs')

    def test_session_controls_and_disconnected_state(self):
        self.page.locator('#btn-start-bypass').click()
        expect(self.page.locator('#session-status')).to_have_text('Analiz oturumu aktif')
        expect(self.page.locator('#btn-start-bypass')).to_be_disabled()
        self.page.locator('#btn-stop-bypass').click()
        expect(self.page.locator('#btn-start-bypass')).to_be_enabled()
        self.status.update(device_connected=False,frida_server_running=False,cert_installed=False,cert_status='no_device',proxy_status='Cihaz Yok')
        expect(self.page.locator('#val-device')).to_have_text('Bağlı değil')
        expect(self.page.locator('#btn-start-bypass')).to_be_disabled()
        self.page.screenshot(path=str(ARTIFACTS/'disconnected.png'),full_page=False)

if __name__ == '__main__': unittest.main()
