"""Bounded APK inspection and conservative script selection; no remote code downloads."""
import re
import hashlib
import zipfile

SIGNATURES = {'okhttp': b'Lokhttp3/CertificatePinner;',
              'trustkit': b'Lcom/datatheorem/android/trustkit/',
              'webview': b'Landroid/webkit/WebView;',
              'cronet': b'Lorg/chromium/net/'}

def inspect_apks(paths):
    found = set()
    budget = 128 * 1024 * 1024
    complete = True
    digest = hashlib.sha256()
    for path in paths:
        item = hashlib.sha256()
        with open(path, 'rb') as source:
            for chunk in iter(lambda: source.read(1024*1024), b''): item.update(chunk)
        digest.update(item.digest())
        with zipfile.ZipFile(path) as apk:
            for entry in apk.infolist():
                name = entry.filename
                if name.endswith('/libflutter.so'): found.add('flutter')
                if name.endswith('/libmonodroid.so') or name.endswith('/libxamarin-app.so'): found.add('xamarin')
                if name.endswith('/libil2cpp.so'): found.add('unity')
                if name.endswith('/libcronet.so'): found.add('cronet')
                if re.fullmatch(r'classes\d*\.dex', name):
                    if entry.file_size > min(budget, 32*1024*1024):
                        complete = False
                        continue
                    budget -= entry.file_size
                    data = apk.read(entry)
                    for label, marker in SIGNATURES.items():
                        if marker in data: found.add(label)
    unsupported = sorted(found & {'flutter','xamarin','unity','cronet'})
    return {'signals': sorted(found), 'complete': complete, 'apk_sha256': digest.hexdigest(),
            'script': None if unsupported or not complete else 'universal_unpin.js',
            'reason': ('Özel/native ağ katmanı: '+', '.join(unsupported)+'. Sürüme özel analiz gerekiyor.') if unsupported else
                      ('APK analizi boyut sınırı nedeniyle tamamlanamadı.' if not complete else
                       'Standart Java TLS scripti seçildi; statik işaretler pinning kanıtı değildir.')}

import base64
import copy
import json
from pathlib import Path
import secrets
import shlex
import tempfile
import threading
import time

class SmartBypass:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.RLock()
        self.state = {'phase':'idle', 'message':'Henüz analiz yapılmadı.'}
        self.nonce = None

    def update(self, **fields):
        with self.lock:
            self.state.update(fields)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        if result.get('phase') in ('starting','waiting') and time.time()-result.get('started_at',0) > 90:
            result.update(phase='unverified', message='90 saniye içinde doğrulanmış HTTPS yanıtı gözlenmedi. Bu sonuç başarı değildir.')
        return result

    def event(self, line):
        with self.lock:
            if not self.nonce or self.state.get('phase') not in ('starting','waiting','verified'):
                return
            marker = 'SMART_EVENT:' + self.nonce + ':'
            if marker not in line: return
            try: data = json.loads(line.split(marker,1)[1].strip())
            except (ValueError, TypeError): return
            if not isinstance(data,dict): return
            if data.get('kind') == 'observer_ready':
                if self.state.get('phase') != 'verified':
                    self.state.update(phase='waiting', observer_hooks=data.get('hooks',0),
                        message='Uygulamada ağ isteği oluşturun. Hedef süreçten HTTPS doğrulaması bekleniyor.')
            elif data.get('kind') == 'https_response':
                host, code = data.get('host'), data.get('code')
                if not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9.:-]{1,253}',host): return
                if not isinstance(code,int) or isinstance(code,bool) or not 100 <= code <= 599: return
                self.state.update(phase='verified', host=host, http_status=code,
                    message='Hedef süreçte Mitmweb CA imzalı HTTPS yanıtı doğrulandı. Bu, önceden pinning bulunduğunu kanıtlamaz.')
                plan = self.state.get('plan',{})
                if plan.get('apk_sha256') and self.state.get('package'):
                    try:
                        folder = Path(self.manager.project_dir)/'artifacts'/'smart-profiles'
                        folder.mkdir(parents=True,exist_ok=True)
                        profile = {'package':self.state['package'],'apk_sha256':plan['apk_sha256'],
                                   'script':plan['script'],'confirmed_at':time.time()}
                        (folder/(self.state['package']+'.json')).write_text(json.dumps(profile),encoding='utf-8')
                    except OSError:
                        pass # A read-only profile folder must not break the target's session.

    def ended(self):
        with self.lock:
            if self.nonce:
                self.state.update(phase='stopped', message='Akıllı bypass oturumu durdu. Son kanıt varsa ayrıntılarda korunur.')
                self.nonce = None

    def start(self, package, proxy_ip, proxy_port):
        m = self.manager
        if not isinstance(package,str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+',package):
            return {'success':False,'error':'Geçerli bir uygulama paketi seçin.'}
        if m.bypass_running:
            return {'success':False,'error':'Önce çalışan analiz oturumunu durdurun.'}
        self.nonce = secrets.token_hex(16)
        self.state = {'phase':'analyzing','package':package,'started_at':time.time(),
                      'message':'APK ve cihaz analiz ediliyor.'}
        try:
            if not m.check_devices(): raise RuntimeError('Cihaz bağlı değil.')
            result = m.adb('shell', 'pm path '+package, timeout=5)
            paths = [line[8:].strip() for line in result.stdout.splitlines() if line.startswith('package:')]
            if result.returncode or not paths: raise RuntimeError('Uygulama APK yolu okunamadı.')
            if len(paths)>24: raise RuntimeError('APK bölümü sınırı aşıldı.')
            with tempfile.TemporaryDirectory(prefix='smart-apk-') as temp:
                local = []
                deadline = time.monotonic() + 60
                total_size = 0
                for index, remote in enumerate(paths):
                    if not remote.startswith('/data/app/') or not remote.endswith('.apk') or '..' in remote:
                        raise RuntimeError('APK yolu beklenen uygulama dizininde değil.')
                    if time.monotonic() >= deadline: raise RuntimeError('APK analiz süresi aşıldı.')
                    size = m.adb('shell', 'stat -c %s '+shlex.quote(remote), timeout=3)
                    if size.returncode or not size.stdout.strip().isdigit(): raise RuntimeError('APK boyutu okunamadı.')
                    total_size += int(size.stdout.strip())
                    if total_size > 256*1024*1024: raise RuntimeError('APK toplam boyutu 256 MB analiz sınırını aşıyor.')
                    target = str(Path(temp)/f'{index}.apk')
                    pulled = m.adb('pull', remote, target, timeout=20)
                    if pulled.returncode: raise RuntimeError('APK analiz için okunamadı.')
                    local.append(target)
                plan = inspect_apks(local)
            self.update(plan=plan)
            profile_path = Path(m.project_dir)/'artifacts'/'smart-profiles'/(package+'.json')
            try:
                profile = json.loads(profile_path.read_text(encoding='utf-8'))
                self.update(profile_reused=isinstance(profile,dict) and profile.get('apk_sha256') == plan['apk_sha256'] and profile.get('script') == plan['script'])
            except (OSError, ValueError):
                self.update(profile_reused=False)
            if not plan['script']:
                self.update(phase='unsupported',message=plan['reason'])
                return {'success':False,'error':plan['reason'],'smart':self.snapshot()}
            if not m.start_frida_server(): raise RuntimeError('Frida başlatılamadı.')
            ok, message = m.start_mitmweb()
            if not ok: raise RuntimeError(message)
            if not m.set_proxy(proxy_ip,proxy_port): raise RuntimeError('Proxy açılamadı.')
            if not m.check_cert_status(force=True)[1]:
                ok, message = m.install_mitm_cert()
                if not ok: raise RuntimeError(message)
            ca_path = Path.home()/'.mitmproxy'/'mitmproxy-ca-cert.pem'
            ca = base64.b64encode(ca_path.read_bytes()).decode('ascii')
            observer = (Path(m.project_dir)/'_smart_observer.js').read_text(encoding='utf-8')
            source = 'const SMART_CA='+json.dumps(ca)+'; const SMART_NONCE='+json.dumps(self.nonce)+';\n'+observer
            self.update(phase='starting',started_at=time.time(),message='Script seçildi; uygulama başlatılıyor. HTTPS henüz doğrulanmadı.')
            if not m.start_bypass(package,plan['script'],smart_source=source):
                raise RuntimeError('Oturum başlatılamadı.')
            return {'success':True,'smart':self.snapshot()}
        except Exception as exc:
            self.update(phase='failed',message=str(exc))
            return {'success':False,'error':str(exc),'smart':self.snapshot()}
