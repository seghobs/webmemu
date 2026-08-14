import os
import subprocess
import threading
import queue
import time
import glob
from datetime import datetime
from flask import Flask, jsonify, request, render_template, Response

app = Flask(__name__)

class BypassManager:
    def __init__(self):
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.memu_dir = self.find_memu_dir()
        if self.memu_dir:
            self.adb_path = os.path.join(self.memu_dir, "adb.exe")
        else:
            self.adb_path = r"C:\Program Files\Microvirt\MEmu\adb.exe"
        self.device_connected = False
        self.frida_server_running = False
        self.bypass_running = False
        self.bypass_process = None
        self.extra_processes = []
        self.log_queue = queue.Queue()
        self.log_history = []
        self.proxy_status = "Bilinmiyor"

    def find_memu_dir(self):
        # 1. Try registry
        try:
            import winreg
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MEmu"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microvirt\MEmu"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microvirt\MEmu"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microvirt\MEmu")
            ]
            for hkey, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        for val_name in ["InstallLocation", "InstallPath", "Path", "UninstallString"]:
                            try:
                                path, _ = winreg.QueryValueEx(key, val_name)
                                if path:
                                    if "uninstall" in val_name.lower() or "uninst" in val_name.lower():
                                        path = os.path.dirname(path)
                                    path = path.strip('"')
                                    if os.path.exists(path):
                                        if os.path.exists(os.path.join(path, "MEmu.exe")):
                                            return path
                                        parent = os.path.dirname(path)
                                        if os.path.exists(os.path.join(parent, "MEmu.exe")):
                                            return parent
                            except FileNotFoundError:
                                continue
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Check common paths on all drives
        common_subs = [
            r"Program Files\Microvirt\MEmu",
            r"Program Files (x86)\Microvirt\MEmu",
            r"Microvirt\MEmu",
            r"MEmu"
        ]
        
        # Get drives
        try:
            import string
            from ctypes import windll
            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(letter + ":\\")
                bitmask >>= 1
        except Exception:
            drives = ["C:\\", "D:\\", "E:\\", "F:\\"]
            
        for drive in drives:
            for sub in common_subs:
                check_path = os.path.join(drive, sub)
                if os.path.exists(os.path.join(check_path, "MEmu.exe")):
                    return check_path

        # 3. Last resort search (shallow scan)
        for drive in drives:
            if not os.path.exists(drive):
                continue
            for root_dir in [drive, os.path.join(drive, "Program Files"), os.path.join(drive, "Program Files (x86)")]:
                if not os.path.exists(root_dir):
                    continue
                try:
                    for entry in os.scandir(root_dir):
                        if entry.is_dir() and entry.name.lower() in ["microvirt", "memu"]:
                            if entry.name.lower() == "memu" and os.path.exists(os.path.join(entry.path, "MEmu.exe")):
                                return entry.path
                            for sub_entry in os.scandir(entry.path):
                                if sub_entry.is_dir() and sub_entry.name.lower() == "memu":
                                    if os.path.exists(os.path.join(sub_entry.path, "MEmu.exe")):
                                        return sub_entry.path
                except Exception:
                    pass
        return None

    def start_memu(self):
        if not hasattr(self, 'memu_dir') or not self.memu_dir:
            self.memu_dir = self.find_memu_dir()
            if self.memu_dir:
                self.adb_path = os.path.join(self.memu_dir, "adb.exe")
                
        if not self.memu_dir or not os.path.exists(os.path.join(self.memu_dir, "MEmu.exe")):
            self.log("MEmu Player kurulum dizini bulunamadı!", "error")
            return False, "MEmu Player kurulum dizini bulunamadı!"
            
        memu_exe = os.path.join(self.memu_dir, "MEmu.exe")
        self.log(f"MEmu Player başlatılıyor: {memu_exe}", "process")
        try:
            subprocess.Popen(f'"{memu_exe}"', shell=True)
            self.log("MEmu Player başlatma komutu gönderildi. Cihazın açılması biraz zaman alabilir.", "success")
            
            # Start automatic status checks
            def delayed_checks():
                for i in range(12): # check every 5 seconds for 1 minute
                    time.sleep(5)
                    self.log("Cihaz bağlantısı kontrol ediliyor...", "info")
                    if self.check_devices():
                        self.log("MEmu Player başarıyla bağlandı ve hazır hale geldi!", "success")
                        break
            threading.Thread(target=delayed_checks, daemon=True).start()
            
            return True, "MEmu Player başlatılıyor..."
        except Exception as e:
            self.log(f"MEmu başlatılamadı: {e}", "error")
            return False, str(e)

    def start_mitmweb(self):
        self.log("Mitmweb yeni bir CMD penceresinde başlatılıyor...", "process")
        try:
            subprocess.Popen("start cmd.exe /k \"mitmweb --set web_token=123456 --no-web-open-browser\"", shell=True)
            self.log("Mitmweb başarıyla başlatıldı!", "success")
            
            def open_browser():
                import webbrowser
                time.sleep(2.0)
                webbrowser.open("http://127.0.0.1:8081/?token=123456")
                self.log("Mitmweb arayüzü tarayıcıda otomatik olarak açıldı.", "success")
                
            threading.Thread(target=open_browser, daemon=True).start()
            return True, "Mitmweb başlatıldı!"
        except Exception as e:
            self.log(f"Mitmweb başlatılamadı: {e}", "error")
            return False, str(e)
        
    def log(self, message, level="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon_map = {
            "info": "⚪",
            "success": "🟢",
            "error": "🔴",
            "warning": "🟡",
            "process": "🔵",
            "terminal": "💻"
        }
        icon = icon_map.get(level, "info")
        formatted = f"{icon} [{timestamp}] {message}"
        self.log_history.append(formatted)
        if len(self.log_history) > 100:
            self.log_history.pop(0)
        self.log_queue.put(formatted)

    def check_devices(self):
        # Auto connect to MEmu first
        subprocess.run(f'"{self.adb_path}" connect 127.0.0.1:21503', shell=True, capture_output=True)
        
        result = subprocess.run(f'"{self.adb_path}" devices', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if result.returncode == 0 and "device" in result.stdout:
            lines = [l for l in result.stdout.split('\n') if 'device' in l and 'List' not in l]
            if lines:
                self.device_connected = True
                self.log(f"Cihaz bağlı ({len(lines)} adet)", "success")
                self.check_frida_status()
                self.check_proxy_status()
                return True
        self.device_connected = False
        self.frida_server_running = False
        self.proxy_status = "Cihaz Yok"
        self.log("Cihaz bulunamadı!", "error")
        return False

    def check_frida_status(self):
        if not self.device_connected:
            self.frida_server_running = False
            return
        result = subprocess.run(f'"{self.adb_path}" shell "ps | grep frida"', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if "frida" in result.stdout.lower():
            self.frida_server_running = True
            self.log("Frida server aktif", "success")
        else:
            self.frida_server_running = False
            self.log("Frida server çalışmıyor", "warning")

    def check_proxy_status(self):
        if not self.device_connected:
            self.proxy_status = "Cihaz Yok"
            return
        result = subprocess.run(f'"{self.adb_path}" shell "settings get global http_proxy"', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        proxy_val = result.stdout.strip()
        if not proxy_val or proxy_val == "null" or proxy_val == ":0":
            self.proxy_status = "Kapatıldı"
        else:
            self.proxy_status = proxy_val

    def install_frida_server(self):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Kurulum için cihaz bağlı olmalıdır!", "error")
            return False

        self.log("Cihaz CPU mimarisi sorgulanıyor...", "process")
        # Get CPU ABI
        abi_res = subprocess.run(f'"{self.adb_path}" shell getprop ro.product.cpu.abi', shell=True, capture_output=True, text=True, encoding='utf-8')
        abi = abi_res.stdout.strip().lower()
        self.log(f"Cihaz mimarisi: {abi}", "info")
        
        # Map Android ABI to Frida releases architecture names
        arch_map = {
            "x86_64": "x86_64",
            "x86": "x86",
            "arm64-v8a": "arm64",
            "arm64": "arm64",
            "armeabi-v7a": "arm",
            "armeabi": "arm",
            "arm": "arm"
        }
        
        frida_arch = arch_map.get(abi, "x86_64") # fallback to x86_64
        version = "16.2.2"
        expected_filename = f"frida-server-{version}-android-{frida_arch}"
        local_path = os.path.join(self.project_dir, expected_filename)
        
        # 1. Check if already installed on device
        check_res = subprocess.run(f'"{self.adb_path}" shell "ls /data/local/tmp/frida-server"', shell=True, capture_output=True, text=True)
        if "frida-server" in check_res.stdout:
            # Let's check version
            ver_res = subprocess.run(f'"{self.adb_path}" shell "/data/local/tmp/frida-server --version"', shell=True, capture_output=True, text=True, encoding='utf-8')
            current_ver = ver_res.stdout.strip()
            if current_ver == version:
                self.log(f"Cihazda uyumlu Frida Server ({version}) zaten yüklü.", "success")
                return True
            else:
                self.log(f"Cihazdaki Frida Server sürümü farklı ({current_ver}). Güncelleniyor...", "warning")

        # 2. Look for local binary in project folder
        if os.path.exists(local_path):
            self.log(f"Yerel Frida Server dosyası bulundu: {expected_filename}", "info")
        else:
            # Try finding generic frida-server in project dir
            generic_path = os.path.join(self.project_dir, "frida-server")
            if os.path.exists(generic_path):
                local_path = generic_path
                self.log("Yerel genel 'frida-server' dosyası bulundu.", "info")
            else:
                # 3. Download from GitHub Releases
                download_url = f"https://github.com/frida/frida/releases/download/{version}/frida-server-{version}-android-{frida_arch}.xz"
                xz_path = local_path + ".xz"
                self.log(f"Frida Server {version} indiriliyor: {download_url}", "process")
                
                try:
                    import urllib.request
                    import lzma
                    
                    # Download XZ archive
                    urllib.request.urlretrieve(download_url, xz_path)
                    self.log("İndirme tamamlandı, arşiv açılıyor...", "process")
                    
                    # Decompress XZ to raw binary
                    with lzma.open(xz_path) as f_in:
                        with open(local_path, 'wb') as f_out:
                            f_out.write(f_in.read())
                            
                    # Clean up archive
                    if os.path.exists(xz_path):
                        os.remove(xz_path)
                        
                    self.log("Arşiv başarıyla açıldı ve kaydedildi.", "success")
                except Exception as e:
                    self.log(f"İndirme veya çıkarma hatası: {e}", "error")
                    return False

        # 4. Push binary to device and configure permissions
        self.log("Dosya cihaza yükleniyor (/data/local/tmp/frida-server)...", "process")
        # Kill running instances first
        subprocess.run(f'"{self.adb_path}" shell "su -c \'pkill -9 frida\'"', shell=True, capture_output=True)
        
        push_res = subprocess.run(f'"{self.adb_path}" push "{local_path}" /data/local/tmp/frida-server', shell=True, capture_output=True, text=True)
        if push_res.returncode != 0:
            self.log(f"Dosya yükleme hatası: {push_res.stderr}", "error")
            return False
            
        self.log("Çalıştırma izinleri tanımlanıyor...", "process")
        subprocess.run(f'"{self.adb_path}" shell "su -c \'chmod 755 /data/local/tmp/frida-server\'"', shell=True, capture_output=True)
        
        self.log("Frida Server başarıyla kuruldu!", "success")
        return True

    def start_frida_server(self):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Önce cihaz bağlantısını kontrol edin!", "error")
            return
        
        self.log("Frida server başlatılıyor...", "process")
        def task():
            if not self.install_frida_server():
                self.log("Frida Server kurulum hatası sebebiyle başlatılamadı.", "error")
                return
                
            subprocess.run(f'"{self.adb_path}" shell "su -c \'pkill -9 frida\'"', shell=True, capture_output=True)
            subprocess.Popen(
                f'"{self.adb_path}" shell "su -c \'/data/local/tmp/frida-server -D &\'"',
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            self.log("Frida server başlatma sinyali gönderildi!", "success")
            self.check_frida_status()
        threading.Thread(target=task, daemon=True).start()

    def stop_frida_server(self):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return
        self.log("Frida server durduruluyor...", "process")
        def task():
            subprocess.run(f'"{self.adb_path}" shell "su -c \'pkill -9 frida\'"', shell=True, capture_output=True)
            self.frida_server_running = False
            self.log("Frida server durduruldu", "success")
        threading.Thread(target=task, daemon=True).start()

    def start_bypass(self, app_package, script_name, start_on_profile=False, instagram_username="", bypass_mode="spawn"):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False
        if not self.frida_server_running:
            self.log("Önce Frida server'ı başlatın!", "error")
            return False
        
        # Security check for script path
        script_name = os.path.basename(script_name)
        script_path = os.path.join(self.project_dir, script_name)
        if not os.path.exists(script_path):
            self.log(f"Script bulunamadı: {script_name}", "error")
            return False
            
        self.log(f"Bypass başlatılıyor: {app_package} (Mod: {bypass_mode})", "process")
        self.bypass_running = True
        
        def task():
            if bypass_mode == "attach":
                self.log(f"Uygulama başlatılıyor (Attach modu): {app_package}", "process")
                # Force start app via launcher activity
                self.run_adb_cmd(f"shell monkey -p {app_package} -c android.intent.category.LAUNCHER 1")
                # Wait for process initialization
                time.sleep(2.5)

            if app_package == "com.instagram.android" and start_on_profile:
                target_uri = f"instagram://user?username={instagram_username}" if instagram_username else "instagram://profile"
                def launch_profile():
                    # First attempt (early, during startup)
                    time.sleep(4.0)
                    self.log("Profil yönlendirme tetikleniyor (Aşama 1)...", "process")
                    self.run_adb_cmd(f"shell am start -a android.intent.action.VIEW -d {target_uri} -p com.instagram.android")
                    # Second attempt (after cold-start fully finishes, ensuring override bypass)
                    time.sleep(8.0)
                    self.log("Profil yönlendirme tetikleniyor (Aşama 2)...", "process")
                    self.run_adb_cmd(f"shell am start -a android.intent.action.VIEW -d {target_uri} -p com.instagram.android")
                threading.Thread(target=launch_profile, daemon=True).start()

            def safe_decode(b):
                try:
                    return b.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        return b.decode('cp1254')
                    except UnicodeDecodeError:
                        try:
                            return b.decode('cp857')
                        except UnicodeDecodeError:
                            return b.decode('utf-8', errors='ignore')

            try:
                if bypass_mode == "attach":
                    cmd = f'frida -U -l "{script_path}" -N {app_package}'
                else:
                    cmd = f'frida -U -l "{script_path}" -f {app_package}'
                    
                self.bypass_process = subprocess.Popen(
                    cmd, shell=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
                for line_bytes in iter(self.bypass_process.stdout.readline, b''):
                    if line_bytes:
                        line = safe_decode(line_bytes)
                        clean_line = line.strip()
                        lvl = "info"
                        
                        # Match success prefixes (including literal or escaped checkmarks)
                        success_prefixes = ["[✓]", "[+]", "[\\u2713]", "[\u2713]"]
                        is_success = False
                        for prefix in success_prefixes:
                            if prefix in clean_line:
                                lvl = "success"
                                clean_line = clean_line.replace(prefix, "").strip()
                                is_success = True
                                break
                                
                        if not is_success:
                            # Match error prefixes
                            error_prefixes = ["[-]", "[x]", "Error:", "Exception:"]
                            is_error = False
                            for prefix in error_prefixes:
                                if prefix in clean_line:
                                    lvl = "error"
                                    if prefix in ["[-]", "[x]"]:
                                        clean_line = clean_line.replace(prefix, "").strip()
                                    is_error = True
                                    break
                                    
                            if not is_error:
                                # Match warning prefixes
                                warning_prefixes = ["[!]", "[*]", "[\\u26a0]", "[\u26a0]"]
                                for prefix in warning_prefixes:
                                    if prefix in clean_line:
                                        lvl = "warning"
                                        clean_line = clean_line.replace(prefix, "").strip()
                                        break
                                        
                        self.log(clean_line, lvl)
                    if not self.bypass_running:
                        break
            except Exception as e:
                self.log(f"Bypass Hatası: {e}", "error")
            finally:
                self.log("Bypass sonlandı", "warning")
                self.bypass_running = False
                
        threading.Thread(target=task, daemon=True).start()
        return True

    def stop_bypass(self, app_package=None):
        self.bypass_running = False
        if self.bypass_process:
            try:
                if self.bypass_process.stdout:
                    self.bypass_process.stdout.close()
                if self.bypass_process.stdin:
                    self.bypass_process.stdin.close()
            except:
                pass
            self.bypass_process.terminate()
            self.bypass_process = None
            
        for proc in self.extra_processes:
            try:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stdin:
                    proc.stdin.close()
            except:
                pass
            try:
                proc.terminate()
            except:
                pass
        self.extra_processes = []
        subprocess.run('taskkill /F /IM frida.exe 2>nul', shell=True, capture_output=True)
        if app_package:
            self.run_adb_cmd(f"shell am force-stop {app_package}")
            self.log(f"{app_package} cihazda tamamen kapatıldı.", "success")
        else:
            self.log("Bypass durduruldu", "success")

    def inject_script(self, app_package, script_name):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False
            
        script_name = os.path.basename(script_name)
        script_path = os.path.join(self.project_dir, script_name)
        if not os.path.exists(script_path):
            self.log(f"Script bulunamadı: {script_name}", "error")
            return False
            
        self.log(f"Çalışan uygulamaya ekstra script enjekte ediliyor: {script_name}", "process")
        
        def task():
            try:
                def safe_decode(b):
                    try:
                        return b.decode('utf-8')
                    except UnicodeDecodeError:
                        try:
                            return b.decode('cp1254')
                        except UnicodeDecodeError:
                            try:
                                return b.decode('cp857')
                            except UnicodeDecodeError:
                                return b.decode('utf-8', errors='ignore')

                cmd = f'frida -U -N {app_package} -l "{script_path}"'
                proc = subprocess.Popen(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, bufsize=0
                )
                self.extra_processes.append(proc)
                
                def read_stdout(p):
                    while self.bypass_running and p.poll() is None:
                        line = p.stdout.readline()
                        if not line:
                            break
                        decoded = safe_decode(line)
                        if decoded.strip():
                            clean_line = decoded.strip()
                            lvl = "info"
                            if clean_line.startswith("[*]") or "yüklendi" in clean_line.lower():
                                lvl = "success"
                            elif clean_line.startswith("🔴") or "hata" in clean_line.lower():
                                lvl = "error"
                            elif clean_line.startswith("🟢"):
                                lvl = "success"
                            elif clean_line.startswith("⚪"):
                                lvl = "info"
                            self.log(f"[{script_name}] {clean_line}", lvl)
                            
                threading.Thread(target=read_stdout, args=(proc,), daemon=True).start()
                self.log(f"{script_name} başarıyla enjekte edildi!", "success")
            except Exception as e:
                self.log(f"Enjeksiyon hatası: {e}", "error")
                
        threading.Thread(target=task, daemon=True).start()
        return True

    def get_running_packages(self):
        if not self.device_connected:
            return []
        
        result = subprocess.run(f'"{self.adb_path}" shell dumpsys activity processes', shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if result.returncode != 0:
            return []
            
        import re
        pattern = r"ProcessRecord\{[a-f0-9]+\s+\d+:([^/:]+)"
        matches = re.findall(pattern, result.stdout)
        
        running_pkgs = set()
        for pkg in matches:
            pkg = pkg.strip()
            # Filter out internal/system processes
            if pkg in ["system", "zygote", "zygote64", "init", "adbd", "sh"]:
                continue
            if pkg.startswith("com.android.") or pkg.startswith("android.") or pkg.startswith("com.google.android."):
                continue
            running_pkgs.add(pkg)
            
        return sorted(list(running_pkgs))

    def clean_memu_ads(self):
        if not self.device_connected:
            self.log("Reklamları temizlemek için bir cihaz bağlı olmalıdır!", "error")
            return False
            
        self.log("MEmu reklam ve bloatware hizmetleri analiz ediliyor...", "process")
        ad_packages = [
            "com.microvirt.installer",
            "com.microvirt.guide",
            "com.microvirt.download",
            "com.microvirt.market",
            "com.microvirt.tools"
        ]
        
        cleaned_count = 0
        for pkg in ad_packages:
            # Force stop and disable the package
            subprocess.run(f'"{self.adb_path}" shell am force-stop {pkg}', shell=True, capture_output=True)
            disable_res = subprocess.run(f'"{self.adb_path}" shell pm disable-user --user 0 {pkg}', shell=True, capture_output=True, text=True, encoding='utf-8')
            
            if "new state: disabled" in disable_res.stdout or disable_res.returncode == 0:
                self.log(f"{pkg} (Reklam/Bloatware) kalıcı olarak devre dışı bırakıldı.", "success")
                cleaned_count += 1
            else:
                disable_res2 = subprocess.run(f'"{self.adb_path}" shell pm disable {pkg}', shell=True, capture_output=True)
                if disable_res2.returncode == 0:
                    self.log(f"{pkg} kalıcı olarak devre dışı bırakıldı.", "success")
                    cleaned_count += 1
                else:
                    self.log(f"{pkg} devre dışı bırakılamadı (zaten pasif veya bulunamadı).", "info")
                    
        self.log(f"MEmu Reklam Temizliği tamamlandı! {cleaned_count} hizmet pasifleştirildi.", "success")
        return True

    def run_adb_cmd(self, raw_cmd):
        if not self.device_connected:
            self.log("ADB komutu çalıştırmak için bir cihaz bağlı olmalıdır!", "error")
            return "Hata: Cihaz bağlı değil."
        
        # Strip leading adb shell or adb if typed
        cmd = raw_cmd.strip()
        if cmd.startswith("adb "):
            cmd = cmd[4:].strip()
        if cmd.startswith("shell "):
            cmd = cmd[6:].strip()
            # It's an adb shell command
            full_cmd = f'"{self.adb_path}" shell "{cmd}"'
        else:
            # Normal adb command
            full_cmd = f'"{self.adb_path}" {cmd}'
            
        self.log(f"Komut gönderildi: adb {cmd}", "terminal")
        try:
            result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            output = result.stdout.strip() or result.stderr.strip() or "[Boş Çıktı]"
            for line in output.split('\n'):
                self.log(f"  {line}", "info")
            return output
        except Exception as e:
            self.log(f"ADB Hatası: {e}", "error")
            return f"Hata: {e}"

    def set_proxy(self, proxy_ip, proxy_port):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False
        
        self.log(f"Proxy kuruluyor: {proxy_ip}:{proxy_port}", "process")
        # Run proxy settings commands
        subprocess.run(f'"{self.adb_path}" shell settings put global http_proxy {proxy_ip}:{proxy_port}', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell settings put global global_http_proxy_host {proxy_ip}', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell settings put global global_http_proxy_port {proxy_port}', shell=True, capture_output=True)
        time.sleep(1)
        self.check_proxy_status()
        self.log(f"Proxy aktif edildi: {self.proxy_status}", "success")
        return True

    def disable_proxy(self):
        if not self.device_connected:
            self.log("Cihaz bağlı değil!", "error")
            return False
            
        self.log("Proxy temizleniyor...", "process")
        subprocess.run(f'"{self.adb_path}" shell settings put global http_proxy :0', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell settings delete global http_proxy', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell settings delete global global_http_proxy_host', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell settings delete global global_http_proxy_port', shell=True, capture_output=True)
        time.sleep(1)
        self.check_proxy_status()
        self.log("Proxy kapatıldı ve temizlendi.", "success")
        return True

    def list_packages(self, only_third_party=True):
        friendly_names = {
            "com.instagram.android": "Instagram",
            "com.facebook.katana": "Facebook",
            "com.whatsapp": "WhatsApp",
            "com.twitter.android": "Twitter / X",
            "com.android.chrome": "Google Chrome",
            "org.mozilla.firefox": "Mozilla Firefox",
            "com.spotify.music": "Spotify",
            "com.zhiliaoapp.musically": "TikTok"
        }
        
        # Fallback list if device is not connected
        fallback_list = [
            {"package": pkg, "name": name} for pkg, name in friendly_names.items()
        ]
        
        if not self.device_connected:
            return fallback_list
            
        cmd_suffix = " -3" if only_third_party else ""
        cmd = f'"{self.adb_path}" shell "pm list packages{cmd_suffix}"'
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            if result.returncode != 0:
                return fallback_list
                
            packages = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith("package:"):
                    pkg = line.split(":", 1)[1]
                    name = friendly_names.get(pkg, pkg)
                    packages.append({"package": pkg, "name": name})
            
            # Sort packages by friendly name
            packages.sort(key=lambda x: x["name"].lower())
            
            # If no packages are returned, return fallback list
            if not packages:
                return fallback_list
            return packages
        except Exception as e:
            self.log(f"Paket Listeleme Hatası: {e}", "error")
            return fallback_list

    def get_cert_hash(self, cert_path):
        openssl_paths = [
            r"C:\Program Files\Git\usr\bin\openssl.exe",
            r"C:\Program Files\Git\bin\openssl.exe",
            r"C:\Program Files (x86)\Git\bin\openssl.exe",
            "openssl"
        ]
        
        for openssl in openssl_paths:
            try:
                cmd = f'"{openssl}" x509 -inform PEM -subject_hash_old -in "{cert_path}" -noout'
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                continue
                
        try:
            from cryptography import x509
            with open(cert_path, "rb") as f:
                cert_bytes = f.read()
            cert = x509.load_pem_x509_certificate(cert_bytes)
            import hashlib
            subject_der = cert.subject.public_bytes()
            md5_hash = hashlib.md5(subject_der).digest()
            val = (md5_hash[0] | (md5_hash[1] << 8) | (md5_hash[2] << 16) | (md5_hash[3] << 24))
            return f"{val:08x}"
        except Exception as e:
            self.log(f"Hash hesaplama hatası: {e}", "error")
            return None

    def check_cert_status(self):
        if not self.device_connected:
            return "no_device", False
            
        mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
        subject_hash = None
        if os.path.exists(mitm_cert_path):
            subject_hash = self.get_cert_hash(mitm_cert_path)
            
        # Fallback to standard mitmproxy hash if local file is missing
        if not subject_hash:
            subject_hash = "c8750f0d"
            
        cert_filename = f"{subject_hash}.0"
        result = subprocess.run(
            f'"{self.adb_path}" shell "ls /system/etc/security/cacerts/{cert_filename}"',
            shell=True, capture_output=True, text=True
        )
        if cert_filename in result.stdout:
            return "installed", True
        else:
            return "not_installed", False

    def download_mitm_cert(self):
        proxy_host = None
        proxy_port = None
        if self.proxy_status and ":" in self.proxy_status:
            parts = self.proxy_status.split(":")
            if len(parts) == 2:
                proxy_host = parts[0]
                proxy_port = parts[1]
                
        if not proxy_host or not proxy_port:
            proxy_host = "127.0.0.1"
            proxy_port = "8080"
            
        self.log(f"Sertifika http://mitm.it/cert/pem adresinden indiriliyor... (Proxy: {proxy_host}:{proxy_port})", "process")
        temp_pem_path = os.path.join(self.project_dir, "downloaded_mitm_ca.pem")
        
        try:
            import urllib.request
            proxy_support = urllib.request.ProxyHandler({
                'http': f'http://{proxy_host}:{proxy_port}',
                'https': f'http://{proxy_host}:{proxy_port}'
            })
            opener = urllib.request.build_opener(proxy_support)
            urllib.request.install_opener(opener)
            
            req = urllib.request.Request("http://mitm.it/cert/pem", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                content = response.read()
                
            if b"BEGIN CERTIFICATE" in content:
                with open(temp_pem_path, "wb") as f:
                    f.write(content)
                self.log("Sertifika http://mitm.it üzerinden başarıyla indirildi!", "success")
                return temp_pem_path
            else:
                self.log("İndirilen içerik geçerli bir sertifika değil (mitmproxy çalışıyor mu?).", "error")
                return None
        except Exception as e:
            self.log(f"mitm.it üzerinden indirme başarısız: {e}", "warning")
            return None

    def install_mitm_cert(self):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Sertifika yüklemek için cihaz bağlı olmalıdır!", "error")
            return False, "Cihaz bağlı değil!"
            
        # Try downloading online from mitm.it first
        mitm_cert_path = self.download_mitm_cert()
        is_downloaded = mitm_cert_path is not None
        
        if not mitm_cert_path:
            self.log("Yerel mitmproxy sertifika dosyası aranıyor...", "process")
            mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
            
        if not os.path.exists(mitm_cert_path):
            user_cert_res = subprocess.run(
                f'"{self.adb_path}" shell "ls /data/misc/user/0/cacerts-added/"',
                shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore'
            )
            if "c8750f0d.0" in user_cert_res.stdout:
                self.log("Sertifika emülatörün kullanıcı deposunda bulundu. Sistem deposuna aktarılıyor...", "process")
                subprocess.run(f'"{self.adb_path}" shell "mount -o rw,remount /system"', shell=True, capture_output=True)
                subprocess.run(f'"{self.adb_path}" shell "mount -o rw,remount /"', shell=True, capture_output=True)
                
                cp_res = subprocess.run(
                    f'"{self.adb_path}" shell "cp /data/misc/user/0/cacerts-added/c8750f0d.0 /system/etc/security/cacerts/c8750f0d.0"',
                    shell=True, capture_output=True
                )
                if cp_res.returncode == 0:
                    subprocess.run(f'"{self.adb_path}" shell "chmod 644 /system/etc/security/cacerts/c8750f0d.0"', shell=True, capture_output=True)
                    subprocess.run(f'"{self.adb_path}" shell "mount -o ro,remount /system"', shell=True, capture_output=True)
                    self.log("Sertifika kullanıcı deposundan doğrudan sistem deposuna kopyalandı.", "success")
                    return True, "Sertifika başarıyla kuruldu!"
                else:
                    bind_cmds = (
                        "mkdir -p /dev/tmp/cacerts && "
                        "cp /system/etc/security/cacerts/* /dev/tmp/cacerts/ && "
                        "cp /data/misc/user/0/cacerts-added/c8750f0d.0 /dev/tmp/cacerts/ && "
                        "chmod 644 /dev/tmp/cacerts/* && "
                        "chown root:root /dev/tmp/cacerts/* && "
                        "mount --bind /dev/tmp/cacerts /system/etc/security/cacerts"
                    )
                    bind_res = subprocess.run(f'"{self.adb_path}" shell "{bind_cmds}"', shell=True, capture_output=True)
                    if bind_res.returncode == 0:
                        self.log("Sertifika kullanıcı deposundan bind-mount yöntemiyle sistem deposuna kopyalandı.", "success")
                        return True, "Sertifika başarıyla kuruldu!"
            
            self.log("Hata: Ne mitm.it üzerinden indirilebildi ne de yerel mitmproxy sertifikası bulunabildi!", "error")
            return False, "Yerel mitmproxy sertifikası veya mitm.it adresi bulunamadı!"
            
        subject_hash = self.get_cert_hash(mitm_cert_path)
        if not subject_hash:
            if is_downloaded:
                try: os.remove(mitm_cert_path)
                except: pass
            return False, "Sertifika hash değeri hesaplanamadı!"
            
        cert_filename = f"{subject_hash}.0"
        temp_device_path = f"/data/local/tmp/{cert_filename}"
        system_cacerts_path = f"/system/etc/security/cacerts/{cert_filename}"
        
        self.log("mitmproxy sertifikası yükleniyor...", "process")
        
        # 1. Push cert to temporary folder
        push_res = subprocess.run(f'"{self.adb_path}" push "{mitm_cert_path}" {temp_device_path}', shell=True, capture_output=True, text=True)
        
        # Clean up local temporary download file
        if is_downloaded:
            try: os.remove(mitm_cert_path)
            except: pass
            
        if push_res.returncode != 0:
            self.log(f"Sertifika cihaza kopyalanamadı: {push_res.stderr}", "error")
            return False, "Dosya kopyalama hatası!"
            
        # 2. Try normal direct copy first (if system is already RW)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /system\'"', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /\'"', shell=True, capture_output=True)
        
        cp_res = subprocess.run(f'"{self.adb_path}" shell "su -c \'cp {temp_device_path} {system_cacerts_path}\'"', shell=True, capture_output=True)
        if cp_res.returncode == 0:
            subprocess.run(f'"{self.adb_path}" shell "su -c \'chmod 644 {system_cacerts_path}\'"', shell=True, capture_output=True)
            subprocess.run(f'"{self.adb_path}" shell "su -c \'rm {temp_device_path}\'"', shell=True, capture_output=True)
            subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o ro,remount /system\'"', shell=True, capture_output=True)
            self.log("Sertifika doğrudan /system dizinine kopyalandı.", "success")
        else:
            # 3. If direct copy failed (Read-only fs), use the bind-mount trick!
            self.log("Dizin korumalı, bind-mount (geçici bellek bağlama) yöntemi kullanılıyor...", "warning")
            bind_cmds = (
                f"mkdir -p /dev/tmp/cacerts && "
                f"cp /system/etc/security/cacerts/* /dev/tmp/cacerts/ && "
                f"cp {temp_device_path} /dev/tmp/cacerts/ && "
                f"chmod 644 /dev/tmp/cacerts/* && "
                f"chown root:root /dev/tmp/cacerts/* && "
                f"mount --bind /dev/tmp/cacerts /system/etc/security/cacerts"
            )
            bind_res = subprocess.run(f'"{self.adb_path}" shell "su -c \'{bind_cmds}\'"', shell=True, capture_output=True, text=True)
            subprocess.run(f'"{self.adb_path}" shell "su -c \'rm -f {temp_device_path}\'"', shell=True, capture_output=True)
            
            if bind_res.returncode != 0:
                self.log(f"Bind-mount hatası: {bind_res.stderr}", "error")
                return False, "Sertifika kurulumu başarısız!"
                
        # 4. Check if successfully installed
        check_res = subprocess.run(f'"{self.adb_path}" shell "ls {system_cacerts_path}"', shell=True, capture_output=True, text=True)
        if cert_filename in check_res.stdout:
            self.log(f"mitmproxy CA sertifikası ({cert_filename}) başarıyla kuruldu!", "success")
            return True, "Sertifika başarıyla yüklendi!"
        else:
            self.log("Sertifika kurulumu doğrulanmadı!", "error")
            return False, "Sertifika kopyalanamadı!"
            
    def remove_mitm_cert(self):
        if not self.device_connected:
            self.check_devices()
        if not self.device_connected:
            self.log("Sertifika silmek için cihaz bağlı olmalıdır!", "error")
            return False, "Cihaz bağlı değil!"
            
        mitm_cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.pem")
        if not os.path.exists(mitm_cert_path):
            self.log("Yerel mitmproxy sertifikası bulunamadı, varsayılan hash 'c8750f0d' kullanılıyor.", "warning")
            subject_hash = "c8750f0d"
        else:
            subject_hash = self.get_cert_hash(mitm_cert_path)
            if not subject_hash:
                subject_hash = "c8750f0d"
                
        cert_filename = f"{subject_hash}.0"
        system_cacerts_path = f"/system/etc/security/cacerts/{cert_filename}"
        
        self.log(f"mitmproxy sertifikası ({cert_filename}) kaldırılıyor...", "process")
        
        # 1. Try unmounting bind mount first
        subprocess.run(f'"{self.adb_path}" shell "su -c \'umount /system/etc/security/cacerts\'"', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'rm -rf /dev/tmp/cacerts\'"', shell=True, capture_output=True)
        
        # 2. Also try direct deletion in case it was written directly
        subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /system\'"', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o rw,remount /\'"', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'rm -f {system_cacerts_path}\'"', shell=True, capture_output=True)
        subprocess.run(f'"{self.adb_path}" shell "su -c \'mount -o ro,remount /system\'"', shell=True, capture_output=True)
        
        # 3. Check if successfully removed
        check_res = subprocess.run(f'"{self.adb_path}" shell "ls {system_cacerts_path}"', shell=True, capture_output=True, text=True)
        if cert_filename not in check_res.stdout:
            self.log("mitmproxy CA sertifikası başarıyla kaldırıldı.", "success")
            return True, "Sertifika kaldırıldı."
        else:
            self.log("Sertifika kaldırılamadı! Sistem dizini korumalı veya mount çözülemedi.", "error")
            return False, "Sertifika silinemedi!"

manager = BypassManager()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    cert_status, cert_installed = manager.check_cert_status()
    return jsonify({
        "device_connected": manager.device_connected,
        "frida_server_running": manager.frida_server_running,
        "bypass_running": manager.bypass_running,
        "proxy_status": manager.proxy_status,
        "cert_status": cert_status,
        "cert_installed": cert_installed
    })

@app.route('/api/install_cert', methods=['POST'])
def install_cert():
    success, msg = manager.install_mitm_cert()
    return jsonify({"success": success, "message": msg})

@app.route('/api/remove_cert', methods=['POST'])
def remove_cert():
    success, msg = manager.remove_mitm_cert()
    return jsonify({"success": success, "message": msg})

@app.route('/api/log_frontend_error', methods=['POST'])
def log_frontend_error():
    data = request.json or {}
    msg = data.get("message")
    line = data.get("lineno")
    err = data.get("error")
    manager.log(f"FRONTEND HATA: {msg} (Satır: {line})", "error")
    if err:
        for l in err.split('\n'):
            manager.log(f"  {l}", "error")
    return jsonify({"success": True})

@app.route('/api/check_devices', methods=['POST'])
def check_devices():
    manager.check_devices()
    return get_status()

@app.route('/api/start_memu', methods=['POST'])
def start_memu():
    success, msg = manager.start_memu()
    return jsonify({"success": success, "message": msg})

@app.route('/api/start_mitmweb', methods=['POST'])
def start_mitmweb():
    success, msg = manager.start_mitmweb()
    return jsonify({"success": success, "message": msg})

@app.route('/api/start_frida', methods=['POST'])
def start_frida():
    manager.start_frida_server()
    return jsonify({"status": "starting"})

@app.route('/api/stop_frida', methods=['POST'])
def stop_frida():
    manager.stop_frida_server()
    return jsonify({"status": "stopping"})

@app.route('/api/start_bypass', methods=['POST'])
def start_bypass():
    data = request.json or {}
    app_package = data.get("app_package", "com.instagram.android")
    script_name = data.get("script_name", "nev1.js")
    start_on_profile = data.get("start_on_profile", False)
    instagram_username = data.get("instagram_username", "")
    bypass_mode = data.get("bypass_mode", "spawn")
    
    success = manager.start_bypass(app_package, script_name, start_on_profile, instagram_username, bypass_mode)
    return jsonify({"success": success, "status": "running" if success else "failed"})

@app.route('/api/stop_bypass', methods=['POST'])
def stop_bypass():
    data = request.json or {}
    app_package = data.get("app_package", "")
    manager.stop_bypass(app_package)
    return jsonify({"success": True, "status": "stopped"})

@app.route('/api/inject_script', methods=['POST'])
def inject_script():
    data = request.json or {}
    app_package = data.get("app_package", "com.instagram.android")
    script_name = data.get("script_name", "")
    if not script_name:
        return jsonify({"success": False, "error": "Script adı belirtilmelidir."})
    success = manager.inject_script(app_package, script_name)
    return jsonify({"success": success})

@app.route('/api/list_running_packages', methods=['GET'])
def list_running_packages():
    pkgs = manager.get_running_packages()
    return jsonify({"success": True, "packages": pkgs})

@app.route('/api/force_stop_package', methods=['POST'])
def force_stop_package():
    data = request.json or {}
    app_package = data.get("app_package", "")
    if app_package:
        manager.run_adb_cmd(f"shell am force-stop {app_package}")
        manager.log(f"{app_package} sonlandırıldı.", "success")
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Geçersiz paket"})

@app.route('/api/clean_ads', methods=['POST'])
def clean_ads():
    success = manager.clean_memu_ads()
    return jsonify({"success": success})

@app.route('/api/list_scripts', methods=['GET'])
def list_scripts():
    pattern = os.path.join(manager.project_dir, "*.js")
    js_files = glob.glob(pattern)
    basenames = [os.path.basename(f) for f in js_files]
    return jsonify({"success": True, "scripts": basenames})

@app.route('/api/list_packages', methods=['GET'])
def list_packages():
    only_third_party = request.args.get("third_party", "true").lower() == "true"
    packages = manager.list_packages(only_third_party)
    return jsonify({"success": True, "packages": packages})

@app.route('/api/read_script', methods=['GET'])
def read_script():
    script_name = request.args.get("script_name", "nev1.js")
    script_name = os.path.basename(script_name)
    script_path = os.path.join(manager.project_dir, script_name)
    
    if not os.path.exists(script_path):
        return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
        
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"success": True, "content": content})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/save_script', methods=['POST'])
def save_script():
    data = request.json or {}
    script_name = data.get("script_name", "nev1.js")
    content = data.get("content", "")
    
    script_name = os.path.basename(script_name)
    script_path = os.path.join(manager.project_dir, script_name)
    
    try:
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(content)
        manager.log(f"Script kaydedildi: {script_name}", "success")
        return jsonify({"success": True})
    except Exception as e:
        manager.log(f"Script kaydedilemedi: {e}", "error")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/run_adb', methods=['POST'])
def run_adb():
    data = request.json or {}
    cmd = data.get("command", "")
    if not cmd:
        return jsonify({"success": False, "error": "Boş komut"})
    
    output = manager.run_adb_cmd(cmd)
    return jsonify({"success": True, "output": output})

@app.route('/api/toggle_proxy', methods=['POST'])
def toggle_proxy():
    data = request.json or {}
    enabled = data.get("enabled", False)
    proxy_ip = data.get("proxy_ip", "192.168.31.61")
    proxy_port = data.get("proxy_port", "8080")
    
    if enabled:
        success = manager.set_proxy(proxy_ip, proxy_port)
    else:
        success = manager.disable_proxy()
        
    return jsonify({"success": success, "proxy_status": manager.proxy_status})

@app.route('/api/get_logs', methods=['GET'])
def get_logs():
    return jsonify({"success": True, "logs": manager.log_history})

@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    manager.log_history = []
    manager.log("Log geçmişi temizlendi.", "info")
    return jsonify({"success": True})

if __name__ == '__main__':
    # Start checking status on launch
    threading.Thread(target=manager.check_devices, daemon=True).start()
    # Run server locally on port 5000
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
