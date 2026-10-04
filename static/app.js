// Keep each request bounded and each button tied to its real operation.
const nativeFetch = window.fetch.bind(window);
function apiFetch(url, options = {}) {
    return nativeFetch(url, { ...options, signal: options.signal || AbortSignal.timeout(url === '/api/install_apk' ? 180000 : 8000) });
}
let clickButton = null;
document.addEventListener('click', event => {
    clickButton = event.target.closest('button');
    setTimeout(() => { clickButton = null; }, 0);
}, true);
const busyButtons = new WeakMap();
function buttonBusy(button, busy) {
    if (!button) return;
    if (busy) {
        if (!busyButtons.has(button)) busyButtons.set(button, { count: 0, html: button.innerHTML });
        const state = busyButtons.get(button); state.count++;
        button.dataset.busy = 'true'; button.setAttribute('aria-busy', 'true'); button.disabled = true;
        button.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> İşleniyor…';
    } else {
        const state = busyButtons.get(button);
        if (!state || --state.count > 0) return;
        button.innerHTML = state.html; button.disabled = false;
        button.removeAttribute('aria-busy'); delete button.dataset.busy; busyButtons.delete(button);
    }
}
async function finishJob(data) {
    if (!data.job_id) return data;
    const deadline = performance.now() + 180000;
    while (performance.now() < deadline) {
        const response = await apiFetch('/api/jobs/' + data.job_id);
        if (!response.ok) throw new Error('İşlem durumu okunamadı.');
        const job = await response.json();
        if (job.state === 'completed' || job.state === 'failed') return job.result;
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('İşlem sürüyor; sonucu loglardan takip edebilirsiniz.');
}
let packagesPending = false, runningPending = false, logsPending = false;
let lastPackagesKey = '', lastRunningKey = '', lastDeviceConnected = null;
let packagesRetry;
// Small, accessible feedback surface shared by non-destructive actions.
function showNotice(message, isError = false) {
    let notice = document.getElementById('action-notice');
    if (!notice) {
        notice = document.createElement('div');
        notice.id = 'action-notice'; notice.className = 'notice';
        notice.setAttribute('role', 'status'); document.body.appendChild(notice);
    }
    notice.textContent = message; notice.dataset.error = String(isError); notice.hidden = false;
    clearTimeout(showNotice.timer); showNotice.timer = setTimeout(() => { notice.hidden = true; }, 6000);
}
let statusPending = false;
let editorDirty = false;

        // Global Frontend Error Logging
        window.onerror = function(message, source, lineno, colno, error) {
            apiFetch('/api/log_frontend_error', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    source: source,
                    lineno: lineno,
                    colno: colno,
                    error: error ? error.stack : ''
                })
            }).catch(() => {});
        };

        // Custom Dropdown JS Manager Class
        class CustomSelect {
            constructor(elementId, onChangeCallback) {
                this.container = document.getElementById(elementId);
                this.trigger = this.container.querySelector('.custom-select-trigger');
                this.triggerText = this.container.querySelector('.custom-select-trigger-text');
                this.dropdown = this.container.querySelector('.custom-select-dropdown');
                this.searchInput = this.container.querySelector('.custom-select-search-input');
                this.optionsContainer = this.container.querySelector('.custom-select-options');
                this.onChange = onChangeCallback;

                this.value = '';
                this.options = []; // list of { value, text, subtitle }

                this.trigger.setAttribute('aria-expanded', 'false');
                this.trigger.setAttribute('aria-label', elementId === 'dropdown-app' ? 'Hedef uygulama' : 'Aktif script');
                this.searchInput.setAttribute('aria-label', elementId === 'dropdown-app' ? 'Uygulama ara' : 'Script ara');
                this.initEvents();
            }

            initEvents() {
                this.trigger.addEventListener('click', (e) => {
                    e.stopPropagation();
                    document.querySelectorAll('.custom-select-dropdown').forEach(d => {
                        if (d !== this.dropdown) { d.classList.remove('open'); d.parentElement.querySelector('button').setAttribute('aria-expanded', 'false'); }
                    });
                    this.dropdown.classList.toggle('open');
                    this.trigger.setAttribute('aria-expanded', String(this.dropdown.classList.contains('open')));
                    if (this.dropdown.classList.contains('open')) {
                        this.searchInput.focus();
                    }
                });

                this.searchInput.addEventListener('input', () => {
                    const query = this.searchInput.value.trim().toLowerCase();
                    this.filterOptions(query);
                });

                document.addEventListener('click', () => {
                    this.dropdown.classList.remove('open');
                    this.trigger.setAttribute('aria-expanded', 'false');
                });

                this.container.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') { this.dropdown.classList.remove('open'); this.trigger.setAttribute('aria-expanded', 'false'); this.trigger.focus(); }
                    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                        e.preventDefault();
                        if (!this.dropdown.classList.contains('open')) this.trigger.click();
                        const items = Array.from(this.optionsContainer.children);
                        const current = items.indexOf(document.activeElement);
                        const next = e.key === 'ArrowDown' ? (current + 1) % items.length : (current - 1 + items.length) % items.length;
                        if (items[next]) items[next].focus();
                    }
                });
                this.dropdown.addEventListener('click', (e) => {
                    e.stopPropagation();
                });
            }

            setOptions(optionsList) {
                this.options = optionsList;
                this.renderOptions(optionsList);
            }

            renderOptions(list) {
                this.optionsContainer.innerHTML = '';
                if (list.length === 0) {
                    const empty = document.createElement('div');
                    empty.className = 'custom-select-no-options';
                    empty.textContent = 'Sonuç bulunamadı';
                    this.optionsContainer.appendChild(empty);
                    return;
                }

                list.forEach(opt => {
                    const item = document.createElement('div');
                    item.className = 'custom-select-option';
                    item.tabIndex = 0; item.setAttribute('role', 'button');
                    item.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); item.click(); } });
                    if (opt.value === this.value) {
                        item.classList.add('selected');
                    }

                    const title = document.createElement('div');
                    title.className = 'option-title';
                    title.textContent = opt.text;
                    item.appendChild(title);

                    if (opt.subtitle) {
                        const sub = document.createElement('div');
                        sub.className = 'option-subtitle';
                        sub.textContent = opt.subtitle;
                        item.appendChild(sub);
                    }

                    item.addEventListener('click', () => {
                        this.setValue(opt.value, opt.text);
                        this.trigger.setAttribute('aria-expanded', 'false');
                        this.trigger.focus();
                        this.dropdown.classList.remove('open');
                    });

                    this.optionsContainer.appendChild(item);
                });
            }

            filterOptions(query) {
                let filtered = this.options.filter(opt =>
                    opt.text.toLowerCase().includes(query) ||
                    (opt.subtitle && opt.subtitle.toLowerCase().includes(query)) ||
                    opt.value.toLowerCase().includes(query)
                );

                // Dynamic addition of custom typed package/script names
                if (query && !this.options.some(opt => opt.value.toLowerCase() === query)) {
                    filtered.push({
                        value: this.searchInput.value.trim(),
                        text: `➕ Özel Ekle: "${this.searchInput.value.trim()}"`,
                        subtitle: 'Listede olmayan özel değer olarak seç'
                    });
                }
                this.renderOptions(filtered);
            }

            setValue(val, textOverride = '') {
                if (this.container.id === 'dropdown-script' && val !== this.value && editorDirty && !confirm('Kaydedilmemiş değişiklikler var. Script değiştirilsin mi?')) return;
                this.value = val;
                if (!textOverride) {
                    const found = this.options.find(o => o.value === val);
                    textOverride = found ? found.text : val;
                }

                // If it's a dynamic custom option, extract clean text
                if (textOverride.startsWith('➕ Özel Ekle:')) {
                    textOverride = val;
                }

                this.triggerText.textContent = textOverride;

                // Refresh selection visual state
                this.renderOptions(this.options);
                this.searchInput.value = '';

                if (this.onChange) {
                    this.onChange(val);
                }
            }
        }

        // DOM Elements
        const dotDevice = document.getElementById('dot-device');
        const valDevice = document.getElementById('val-device');
        const dotFrida = document.getElementById('dot-frida');
        const valFrida = document.getElementById('val-frida');
        const dotProxy = document.getElementById('dot-proxy');
        const valProxy = document.getElementById('val-proxy');
        const dotCert = document.getElementById('dot-cert');
        const valCert = document.getElementById('val-cert');

        const btnStartFrida = document.getElementById('btn-start-frida');
        const btnStopFrida = document.getElementById('btn-stop-frida');
        const btnStartBypass = document.getElementById('btn-start-bypass');
        const btnStopBypass = document.getElementById('btn-stop-bypass');
        const btnInjectScript = document.getElementById('btn-inject-script');
        const btnRefresh = document.getElementById('btn-refresh');
        const btnStartMemu = document.getElementById('btn-start-memu');

        const proxyIp = document.getElementById('proxy-ip');
        const proxyPort = document.getElementById('proxy-port');
        const btnProxyOn = document.getElementById('btn-proxy-on');
        const btnProxyOff = document.getElementById('btn-proxy-off');
        const btnStartMitmweb = document.getElementById('btn-start-mitmweb');
        const btnCertInstall = document.getElementById('btn-cert-install');
        const btnCertRemove = document.getElementById('btn-cert-remove');

        const scriptEditor = document.getElementById('script-editor');
        const editorFilename = document.getElementById('editor-filename');
        const btnSaveScript = document.getElementById('btn-save-script');

        const consoleOutput = document.getElementById('console-output');
        const btnClearConsole = document.getElementById('btn-clear-console');
        const adbCmdInput = document.getElementById('adb-cmd-input');

        const checkThirdParty = document.getElementById('check-third-party');
        const checkAutoClear = document.getElementById('check-auto-clear');

        // Dropdown Instances
        let appSelect = null;
        let scriptSelect = null;

        // Initialize drop downs
        function initDropdowns() {
            appSelect = new CustomSelect('dropdown-app', (val) => {
                console.log('Uygulama seçildi:', val);
            });

            scriptSelect = new CustomSelect('dropdown-script', (val) => {
                console.log('Script seçildi:', val);
                loadScriptContent(val);
            });
        }

        // Status Polling
        async function updateStatus() {
            if (statusPending) return;
            statusPending = true;
            try {
                const res = await apiFetch('/api/status', { signal: AbortSignal.timeout(6000) });
                if (!res.ok) throw new Error('Durum alınamadı');
                const status = await res.json();

                const pill = document.getElementById('session-status');
                pill.textContent = status.bypass_running ? 'Analiz oturumu aktif' : status.device_connected ? 'Cihaz hazır · Oturum bekleniyor' : 'Cihaz bağlantısı bekleniyor';
                pill.dataset.state = status.bypass_running ? 'running' : 'idle';
                btnStartBypass.disabled = busyButtons.has(btnStartBypass) || !status.device_connected || !status.frida_server_running || status.bypass_running;
                btnStopBypass.disabled = busyButtons.has(btnStopBypass) || !status.bypass_running;
                btnStartFrida.disabled = busyButtons.has(btnStartFrida) || !status.device_connected || status.frida_server_running;
                btnStopFrida.disabled = busyButtons.has(btnStopFrida) || !status.frida_server_running;
                if (lastDeviceConnected !== status.device_connected) {
                    lastDeviceConnected = status.device_connected;
                    lastPackagesKey = '';
                    loadPackagesList();
                }
                if (status.error) { pill.textContent = status.error; pill.dataset.state = 'offline'; }
                // Device connection
                if (status.device_connected) {
                    dotDevice.className = 'status-dot active';
                    valDevice.textContent = 'Bağlı';
                } else {
                    dotDevice.className = 'status-dot inactive';
                    valDevice.textContent = 'Bağlı değil';
                }

                // Frida Server
                if (status.frida_server_running) {
                    dotFrida.className = 'status-dot active';
                    valFrida.textContent = 'Çalışıyor';
                } else {
                    dotFrida.className = 'status-dot inactive';
                    valFrida.textContent = 'Durdu';
                }

                // Proxy status
                if (status.proxy_status === "Kapatıldı") {
                    dotProxy.className = 'status-dot inactive';
                    valProxy.textContent = 'Kapalı';
                } else if (status.proxy_status === "Cihaz Yok") {
                    dotProxy.className = 'status-dot inactive';
                    valProxy.textContent = 'Cihaz Yok';
                } else if (status.proxy_status === "Bilinmiyor") {
                    dotProxy.className = 'status-dot warning';
                    valProxy.textContent = 'Bilinmiyor';
                } else {
                    dotProxy.className = 'status-dot active';
                    valProxy.textContent = status.proxy_status;
                }

                // Cert status
                const certMap = {
                    "unknown": { text: "Doğrulanamadı", dot: "status-dot warning" },
                    "installed": { text: "Kurulu", dot: "status-dot active" },
                    "user_installed": { text: "Kurulu (Kullanıcı)", dot: "status-dot active" },
                    "different_ca": { text: "Farklı CA Kurulu", dot: "status-dot warning" },
                    "not_installed": { text: "Kurulu Değil", dot: "status-dot inactive" },
                    "no_device": { text: "Cihaz Yok", dot: "status-dot warning" },
                    "no_local_cert": { text: "Yerel Sertifika Yok", dot: "status-dot warning" },
                    "hash_failed": { text: "Hash Alınamadı", dot: "status-dot warning" }
                };

                const certInfo = certMap[status.cert_status] || { text: status.cert_status || "Bilinmiyor", dot: "status-dot warning" };
                dotCert.className = certInfo.dot;
                valCert.textContent = certInfo.text;

                if (status.cert_installed) {
                    btnCertInstall.style.display = 'none';
                    btnCertRemove.style.display = 'block';
                } else {
                    btnCertInstall.style.display = 'block';
                    btnCertRemove.style.display = 'none';
                }

                // Stealth Mode status sync
                const checkStealth = document.getElementById('check-stealth-mode');
                if (checkStealth && !checkStealth.dataset.userInteracted) {
                    checkStealth.checked = !!status.stealth_mode;
                }
            } catch (err) {
                document.getElementById('session-status').textContent = 'Sunucuya ulaşılamıyor';
                document.getElementById('session-status').dataset.state = 'offline';
                [btnStartBypass, btnStopBypass, btnStartFrida, btnStopFrida].forEach(button => button.disabled = true);
            } finally {
                statusPending = false;
            }
        }

        // Action POST Util
        async function postAction(url, body = {}, button = clickButton) {
            buttonBusy(button, true);
            try {
                const res = await apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                let data = await res.json();
                if (res.ok) data = await finishJob(data);
                if (!res.ok || data.success === false) showNotice(data.error || data.message || 'İşlem tamamlanamadı.', true);
                return data;
            } catch (err) {
                showNotice(err.name === 'TimeoutError' ? 'Sunucu yanıt süresi aşıldı. Durumu yenileyip tekrar deneyin.' : err.message || 'Bağlantı hatası', true);
                return { success: false, error: err.message };
            } finally {
                buttonBusy(button, false);
                updateStatus();
                fetchLogs();
            }
        }

        // Fetch application packages dynamically from device via adb
        async function loadPackagesList() {
            if (packagesPending) return;
            packagesPending = true;
            const isThirdPartyOnly = checkThirdParty.checked;
            try {
                const res = await apiFetch(`/api/list_packages?third_party=${isThirdPartyOnly}`);
                const data = await res.json();
                if (isThirdPartyOnly !== checkThirdParty.checked) return;
                if (data.refreshing) {
                    clearTimeout(packagesRetry);
                    packagesRetry = setTimeout(loadPackagesList, 350);
                }
                const key = JSON.stringify(data.packages);
                if (key === lastPackagesKey) return;
                lastPackagesKey = key;
                if (data.success && data.packages.length > 0) {
                    const mapped = data.packages.map(pkg => ({
                        value: pkg.package,
                        text: pkg.name,
                        subtitle: pkg.package
                    }));
                    appSelect.setOptions(mapped);

                    // Default to Instagram if it exists, otherwise first app
                    const previous = mapped.find(o => o.value === appSelect.value);
                    const instagramOpt = previous || mapped.find(o => o.value === 'com.instagram.android');
                    if (instagramOpt) {
                        appSelect.setValue(instagramOpt.value, instagramOpt.text);
                    } else {
                        appSelect.setValue(mapped[0].value, mapped[0].text);
                    }
                } else {
                    appSelect.setOptions([]); appSelect.value = "";
                    appSelect.triggerText.textContent = "Uygulama seçin (Cihaz bekleniyor...)";
                }
            } catch (err) {
                console.error('Paketler yüklenemedi:', err);
                appSelect.triggerText.textContent = "Uygulama seçin (Bağlantı hatası)";
            } finally { packagesPending = false; }
        }

        // Script Friendly Names & Descriptions
        const scriptMeta = {
            'nev1.js': { name: 'nev1.js (Meta / IG Bypass)', desc: 'Instagram & Meta SSL Pinning Bypass v5.5' },
            'headers.js': { name: 'headers.js (Header Inspector)', desc: 'x-meta-zca ve Request Header Dinleyici' },
            'device_cloak.js': { name: 'device_cloak.js (S25 Ultra Cloak)', desc: 'Samsung S25 Ultra Donanım Maskeleme & Derin Root Gizleme' },
            'flutter_bypass.js': { name: 'flutter_bypass.js (Flutter SSL)', desc: 'Flutter libflutter.so BoringSSL Bypass' },
            'universal_unpin.js': { name: 'universal_unpin.js (Universal)', desc: 'OkHttp3/4, Conscrypt, TrustKit, OpenSSL Multi-Unpinning' },
            'anti_root_debug.js': { name: 'anti_root_debug.js (Anti-Root/Debug)', desc: 'RootBeer, Su Binary ve Debugger Kontrol Bypass' },
            'webview_devtools.js': { name: 'webview_devtools.js (WebView Inspect)', desc: 'Android WebView Chrome DevTools Uzaktan İnceleme' }
        };

        // Load JS Scripts list from directory
        async function loadScriptsList() {
            try {
                const res = await apiFetch('/api/list_scripts');
                if (!res.ok) throw new Error('API hatası: ' + res.status);
                const data = await res.json();
                if (data.success && data.scripts.length > 0) {
                    const mapped = data.scripts.map(s => {
                        const meta = scriptMeta[s] || { name: s, desc: 'Frida Javascript script' };
                        return {
                            value: s,
                            text: meta.name,
                            subtitle: meta.desc
                        };
                    });
                    scriptSelect.setOptions(mapped);
                    const defaultScript = mapped.find(s => s.value === 'nev1.js') ? 'nev1.js' : mapped[0].value;
                    scriptSelect.setValue(defaultScript);
                } else {
                    setFallbackScripts();
                }
            } catch (err) {
                console.error('Script listesi yüklenemedi, fallback kuruluyor:', err);
                setFallbackScripts();
            }
        }
        // Load running packages list
        async function loadRunningPackagesList() {
            if (runningPending) return;
            runningPending = true;
            const listContainer = document.getElementById('running-apps-list');
            const countBadge = document.getElementById('running-apps-count');
            try {
                const res = await apiFetch('/api/list_running_packages');
                if (!res.ok) throw new Error('API hatası: ' + res.status);
                const data = await res.json();
                if (data.success && data.packages) {
                    const key = JSON.stringify(data.packages);
                    if (key === lastRunningKey) return;
                    lastRunningKey = key;
                    listContainer.innerHTML = '';
                    countBadge.textContent = `${data.packages.length} adet`;

                    if (data.packages.length === 0) {
                        listContainer.innerHTML = '<div style="opacity: 0.4; font-style: italic; text-align: center; padding: 10px 0;">Çalışan uygulama yok</div>';
                        return;
                    }

                    data.packages.forEach(pkg => {
                        const item = document.createElement('div');
                        item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; background: rgba(255, 255, 255, 0.03); padding: 4px 8px; border-radius: 4px; border: 1px solid rgba(255, 255, 255, 0.05);';

                        const textSpan = document.createElement('span');
                        textSpan.style.cssText = 'overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-grow: 1; margin-right: 8px; font-family: Consolas, monospace; font-size: 11px; line-height: 18px; color: rgba(255, 255, 255, 0.8);';
                        textSpan.textContent = pkg;
                        textSpan.title = pkg;

                        const killBtn = document.createElement('button');
                        killBtn.style.cssText = 'background: none; border: none; cursor: pointer; font-size: 11px; padding: 2px 5px; font-weight: bold; transition: all 0.2s; display: flex; align-items: center; justify-content: center;';
                        killBtn.innerHTML = '<i class="fa-solid fa-xmark" style="color: #ffffff !important; font-size: 11px; margin-right: 0; transition: color 0.2s;"></i>';
                        killBtn.title = 'Sonlandır (Force Stop)';

                        killBtn.addEventListener('mouseenter', () => {
                            const icon = killBtn.querySelector('i');
                            if (icon) icon.style.setProperty('color', '#ef4444', 'important');
                        });
                        killBtn.addEventListener('mouseleave', () => {
                            const icon = killBtn.querySelector('i');
                            if (icon) icon.style.setProperty('color', '#ffffff', 'important');
                        });

                        killBtn.addEventListener('click', async (e) => {
                            e.stopPropagation();
                            killBtn.disabled = true;
                            killBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="color: #ef4444 !important; font-size: 11px; margin-right: 0;"></i>';
                            try {
                                const killData = await postAction('/api/force_stop_package', { app_package: pkg }, killBtn);
                                if (killData.success) {
                                    loadRunningPackagesList();
                                }
                            } catch (err) {
                                console.error('Uygulama sonlandırılamadı:', err);
                            }
                        });

                        item.appendChild(textSpan);
                        item.appendChild(killBtn);
                        listContainer.appendChild(item);
                    });
                }
            } catch (err) {
                console.error('Çalışan paket listesi alınamadı:', err);
                listContainer.textContent = 'Uygulama listesi alınamadı.';
            } finally { runningPending = false; }
        }
        function setFallbackScripts() {
            const fallback = [{ value: 'nev1.js', text: 'nev1.js (Varsayılan)', subtitle: 'Varsayılan Frida script' }];
            scriptSelect.setOptions(fallback);
            scriptSelect.setValue('nev1.js');
        }

        // Read Script File Content
        async function loadScriptContent(scriptName) {
            if (!scriptName) return;
            editorFilename.textContent = scriptName;
            editorDirty = false;
            document.getElementById("editor-save-status").textContent = "Yükleniyor…";
            scriptEditor.placeholder = `${scriptName} içeriği yükleniyor...`;
            try {
                const res = await apiFetch(`/api/read_script?script_name=${encodeURIComponent(scriptName)}`);
                if (scriptSelect.value !== scriptName) return;
                if (res.ok) {
                    const data = await res.json();
                    if (scriptSelect.value !== scriptName) return;
                    scriptEditor.value = data.content;
                    document.getElementById('editor-save-status').textContent = 'Kaydedildi';
                    document.getElementById('editor-save-status').dataset.dirty = 'false';
                } else {
                    scriptEditor.value = '';
                    document.getElementById('editor-save-status').textContent = 'Yeni dosya';
                    scriptEditor.placeholder = `Bu script henüz yok. İçeriği yazıp kaydedin.`;
                }
            } catch (err) {
                if (scriptSelect.value !== scriptName) return;
                document.getElementById('editor-save-status').textContent = 'Yüklenemedi';
                scriptEditor.value = '';
                scriptEditor.placeholder = `Dosya yüklenemedi.`;
            }
        }

        // Save Script Content
        async function saveScript() {
            const scriptName = scriptSelect.value;
            const content = scriptEditor.value;
            if (!scriptName) return;
            btnSaveScript.disabled = true;
            btnSaveScript.textContent = '💾 Kaydediliyor...';
            try {
                const res = await apiFetch('/api/save_script', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ script_name: scriptName, content })
                });
                const data = await res.json();
                if (data.success) {
                    editorDirty = scriptEditor.value !== content;
                    document.getElementById('editor-save-status').textContent = editorDirty ? 'Kaydedilmedi' : 'Kaydedildi';
                    document.getElementById('editor-save-status').dataset.dirty = String(editorDirty);
                    showNotice(`${scriptName} kaydedildi.`);
                } else {
                    showNotice('Kaydedilemedi: ' + data.error, true);
                }
            } catch (err) {
                showNotice('İletişim hatası oluştu.', true);
            } finally {
                btnSaveScript.disabled = false;
                btnSaveScript.textContent = '💾 Değişiklikleri Kaydet';
            }
        }
        // Initialize dynamic resizable splitters
        function initResizableSplitters() {
            setupResizer('workspace-resizer', 'editor-pane', 'console-pane', 200, 300);
            setupResizer('console-resizer', 'console-left-pane', 'console-right-pane', 250, 150);
        }

        function setupResizer(resizerId, leftId, rightId, minLeftWidth, minRightWidth) {
            const resizer = document.getElementById(resizerId);
            const leftPane = document.getElementById(leftId);
            const rightPane = document.getElementById(rightId);

            if (!resizer || !leftPane || !rightPane) return;
            resizer.addEventListener('mousedown', (e) => {
                e.preventDefault();
                document.addEventListener('mousemove', handleMouseMove);
                document.addEventListener('mouseup', handleMouseUp);
                resizer.classList.add('dragging');
            });

            function handleMouseMove(e) {
                const container = resizer.parentElement;
                const containerRect = container.getBoundingClientRect();
                const xOffset = e.clientX - containerRect.left;

                if (xOffset >= minLeftWidth && xOffset <= (containerRect.width - minRightWidth)) {
                    const leftRatio = (xOffset / containerRect.width) * 100;
                    const rightRatio = 100 - leftRatio;

                    leftPane.style.flex = leftRatio;
                    rightPane.style.flex = rightRatio;
                }
            }

            function handleMouseUp() {
                document.removeEventListener('mousemove', handleMouseMove);
                document.removeEventListener('mouseup', handleMouseUp);
                resizer.classList.remove('dragging');
            }
        }
        // Poll and render logs
        let lastLogRevision = -1;
        async function fetchLogs() {
            if (logsPending) return;
            logsPending = true;
            try {
                const res = await apiFetch('/api/get_logs');
                const data = await res.json();
                if (data.success && data.revision !== lastLogRevision) {
                    // Update log container
                    consoleOutput.innerHTML = '';
                    data.logs.forEach(text => {
                        const entry = document.createElement('div');
                        entry.className = 'log-entry';

                        // Color coding rules
                        if (text.startsWith('🟢')) {
                            entry.classList.add('log-success');
                        } else if (text.startsWith('🔴')) {
                            entry.classList.add('log-error');
                        } else if (text.startsWith('🟡')) {
                            entry.classList.add('log-warning');
                        } else if (text.startsWith('🔵')) {
                            entry.classList.add('log-process');
                        } else if (text.startsWith('💻')) {
                            entry.classList.add('log-terminal');
                        } else {
                            entry.classList.add('log-info');
                        }

                        entry.textContent = text;
                        consoleOutput.appendChild(entry);
                    });
                    consoleOutput.scrollTop = consoleOutput.scrollHeight;
                    lastLogRevision = data.revision;
                }
            } catch (err) {
                console.error('Loglar alinamadi:', err);
            } finally { logsPending = false; }
        }

        function setupLogStream() {
            // Poll logs every 800ms
            fetchLogs();
            setInterval(() => { if (!document.hidden) fetchLogs(); }, 800);
        }

        // Run ADB Command
        async function runAdbCommand() {
            const cmd = adbCmdInput.value.trim();
            if (!cmd) return;
            adbCmdInput.value = '';

            // Execute in background API
            await postAction('/api/run_adb', { command: cmd });
        }

        // Event Listeners
        btnStartFrida.addEventListener('click', async () => {
            buttonBusy(btnStartFrida, true);
            try {
                const started = await postAction('/api/start_frida', {}, btnStartFrida);
                if (started.success !== false) {
                    await postAction('/api/toggle_proxy', { enabled: true, proxy_ip: proxyIp.value.trim(), proxy_port: proxyPort.value.trim() }, btnStartFrida);
                }
            } finally { buttonBusy(btnStartFrida, false); updateStatus(); }
        });
        btnStopFrida.addEventListener('click', () => postAction('/api/stop_frida'));

        const checkStartOnProfile = document.getElementById('check-start-on-profile');
        const inputProfileUsername = document.getElementById('input-profile-username');

        checkStartOnProfile.addEventListener('change', () => {
            if (checkStartOnProfile.checked) {
                inputProfileUsername.style.display = 'inline-block';
                inputProfileUsername.focus();
            } else {
                inputProfileUsername.style.display = 'none';
            }
        });

        btnStartBypass.addEventListener('click', async () => {
            if (!appSelect.value || !scriptSelect.value) { showNotice('Önce bir uygulama ve script seçin.', true); return; }
            buttonBusy(btnStartBypass, true);
            try {
                if (checkAutoClear.checked) {
                    await postAction('/api/clear_logs', {}, btnStartBypass);
                    lastLogRevision = -1;
                }
                await postAction('/api/start_bypass', {
                    app_package: appSelect.value,
                    script_name: scriptSelect.value,
                    start_on_profile: checkStartOnProfile.checked,
                    instagram_username: inputProfileUsername.value.trim(),
                    bypass_mode: document.getElementById('select-bypass-mode').value,
                    deep_cloak: document.getElementById('check-deep-cloak').checked
                }, btnStartBypass);
            } finally { buttonBusy(btnStartBypass, false); updateStatus(); }
        });

        btnStopBypass.addEventListener('click', () => {
            postAction('/api/stop_bypass', { app_package: appSelect.value });
            setTimeout(loadRunningPackagesList, 1500);
        });

        btnInjectScript.addEventListener('click', () => {
            if (!scriptSelect.value || !appSelect.value) {
                alert("Lütfen enjekte etmek istediğiniz scripti seçin!");
                return;
            }
            const checkDeepCloak = document.getElementById('check-deep-cloak');
            postAction('/api/inject_script', {
                app_package: appSelect.value,
                script_name: scriptSelect.value,
                deep_cloak: checkDeepCloak ? checkDeepCloak.checked : false
            });
        });

        btnRefresh.addEventListener('click', async () => {
            await postAction('/api/check_devices');
            loadPackagesList();
            loadRunningPackagesList();
        });

        btnProxyOn.addEventListener('click', () => {
            postAction('/api/toggle_proxy', {
                enabled: true,
                proxy_ip: proxyIp.value.trim(),
                proxy_port: proxyPort.value.trim()
            });
        });

        btnProxyOff.addEventListener('click', () => {
            postAction('/api/toggle_proxy', { enabled: false });
        });

        btnStartMitmweb.addEventListener('click', async () => {
            btnStartMitmweb.disabled = true;
            const originalHTML = btnStartMitmweb.innerHTML;
            btnStartMitmweb.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="margin-right: 0; color: white !important;"></i> Başlatılıyor...';
            try {
                const res = await postAction('/api/start_mitmweb');
                if (res && res.success) {
                    console.log("Mitmweb başlatıldı:", res.message);
                } else {
                    alert("Mitmweb başlatılamadı: " + (res ? res.message : "Bilinmeyen hata"));
                }
            } catch (err) {
                console.error("Mitmweb başlatma hatası:", err);
            } finally {
                btnStartMitmweb.disabled = false;
                btnStartMitmweb.innerHTML = originalHTML;
            }
        });

        btnCertInstall.addEventListener('click', async () => {
            btnCertInstall.disabled = true;
            const originalHTML = btnCertInstall.innerHTML;
            btnCertInstall.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="margin-right: 4px; color: white !important;"></i> Yükleniyor...';
            try {
                const res = await postAction('/api/install_cert');
                if (res && res.success) {
                    alert("Sertifika başarıyla kuruldu!");
                } else {
                    alert("Sertifika kurulum hatası: " + (res ? res.message : "Bilinmeyen hata"));
                }
            } catch (err) {
                console.error("Sertifika yükleme hatası:", err);
            } finally {
                btnCertInstall.disabled = false;
                btnCertInstall.innerHTML = originalHTML;
            }
        });

        btnCertRemove.addEventListener('click', async () => {
            if (!confirm("mitmproxy CA sertifikasını cihazdan kaldırmak istediğinize emin misiniz?")) return;
            btnCertRemove.disabled = true;
            const originalHTML = btnCertRemove.innerHTML;
            btnCertRemove.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="margin-right: 4px; color: white !important;"></i> Kaldırılıyor...';
            try {
                const res = await postAction('/api/remove_cert');
                if (res && res.success) {
                    alert("Sertifika başarıyla kaldırıldı!");
                } else {
                    alert("Sertifika kaldırma hatası: " + (res ? res.message : "Bilinmeyen hata"));
                }
            } catch (err) {
                console.error("Sertifika kaldırma hatası:", err);
            } finally {
                btnCertRemove.disabled = false;
                btnCertRemove.innerHTML = originalHTML;
            }
        });

        btnStartMemu.addEventListener('click', async () => {
            btnStartMemu.disabled = true;
            const originalHTML = btnStartMemu.innerHTML;
            btnStartMemu.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="margin-right: 0; color: white !important;"></i> Başlatılıyor...';
            try {
                const res = await postAction('/api/start_memu');
                if (res && res.success) {
                    console.log("MEmu başlatıldı:", res.message);
                } else {
                    alert("MEmu başlatılamadı: " + (res ? res.message : "Bilinmeyen hata"));
                }
            } catch (err) {
                console.error("MEmu başlatma hatası:", err);
            } finally {
                btnStartMemu.disabled = false;
                btnStartMemu.innerHTML = originalHTML;
            }
        });

        btnSaveScript.addEventListener('click', saveScript);

        const btnCleanAds = document.getElementById('btn-clean-ads');
        btnCleanAds.addEventListener('click', () => {
            if (confirm("MEmu Player'ın tüm arka plan reklam ve bloatware hizmetlerini kalıcı olarak devre dışı bırakmak istediğinize emin misiniz?")) {
                postAction('/api/clean_ads');
            }
        });

        btnClearConsole.addEventListener('click', () => {
            postAction('/api/clear_logs');
            consoleOutput.innerHTML = '<div class="log-entry log-info">⚪ Log geçmişi temizlendi.</div>';
            lastLogRevision = -1;
        });

        adbCmdInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                runAdbCommand();
            }
        });

        checkThirdParty.addEventListener('change', () => {
            loadPackagesList();
        });

        const checkStealth = document.getElementById('check-stealth-mode');
        if (checkStealth) {
            checkStealth.addEventListener('change', async () => {
                checkStealth.dataset.userInteracted = "true";
                await postAction('/api/toggle_stealth', { enabled: checkStealth.checked });
            });
        }

        // APK / XAPK Drag-and-Drop & Upload Handler
        const dropOverlay = document.getElementById('drop-overlay');
        const uploadToast = document.getElementById('upload-toast');
        const uploadToastText = document.getElementById('upload-toast-text');
        const btnUploadApk = document.getElementById('btn-upload-apk');
        const apkFileInput = document.getElementById('apk-file-input');

        let dragCounter = 0;

        window.addEventListener('dragenter', (e) => {
            e.preventDefault();
            dragCounter++;
            if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
                dropOverlay.classList.add('active');
            }
        });

        window.addEventListener('dragleave', (e) => {
            e.preventDefault();
            dragCounter--;
            if (dragCounter <= 0) {
                dragCounter = 0;
                dropOverlay.classList.remove('active');
            }
        });

        window.addEventListener('dragover', (e) => {
            e.preventDefault();
        });

        window.addEventListener('drop', (e) => {
            e.preventDefault();
            dragCounter = 0;
            dropOverlay.classList.remove('active');

            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                uploadPackageFile(file);
            }
        });

        btnUploadApk.addEventListener('click', () => {
            apkFileInput.click();
        });

        apkFileInput.addEventListener('change', () => {
            if (apkFileInput.files && apkFileInput.files.length > 0) {
                uploadPackageFile(apkFileInput.files[0]);
                apkFileInput.value = '';
            }
        });

        async function uploadPackageFile(file) {
            const ext = file.name.split('.').pop().toLowerCase();
            if (!['apk', 'xapk', 'apks'].includes(ext)) {
                alert('Lütfen sadece .apk, .xapk veya .apks uzantılı bir dosya seçin!');
                return;
            }

            uploadToastText.textContent = `"${file.name}" yükleniyor, lütfen bekleyin...`;
            uploadToast.style.display = 'flex';

            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await apiFetch('/api/install_apk', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();

                if (data.success) {
                    uploadToastText.textContent = `🟢 ${data.message || 'Yükleme başarılı!'}`;
                    setTimeout(() => { uploadToast.style.display = 'none'; }, 4000);
                    // Uygulama listesini yenile
                    await loadPackagesList();
                } else {
                    uploadToastText.textContent = `🔴 Hata: ${data.message || data.error || 'Yüklenemedi'}`;
                    setTimeout(() => { uploadToast.style.display = 'none'; }, 6000);
                }
            } catch (err) {
                uploadToastText.textContent = `🔴 Yükleme sırasında bağlantı hatası oluştu.`;
                setTimeout(() => { uploadToast.style.display = 'none'; }, 5000);
            }
        }

        // Quick Actions Event Listeners
        const btnQuickClear = document.getElementById('btn-quick-clear');
        const btnQuickRestart = document.getElementById('btn-quick-restart');
        const btnQuickGrant = document.getElementById('btn-quick-grant');

        if (btnQuickClear) {
            btnQuickClear.addEventListener('click', async () => {
                const pkg = appSelect ? appSelect.value : '';
                if (!pkg) { alert('Lütfen önce bir uygulama seçin!'); return; }
                if (confirm(`"${pkg}" uygulamasının tüm verilerini ve önbelleğini sıfırlamak istediğinize emin misiniz?`)) {
                    btnQuickClear.disabled = true;
                    await postAction('/api/quick_action/clear_data', { app_package: pkg });
                    btnQuickClear.disabled = false;
                }
            });
        }

        if (btnQuickRestart) {
            btnQuickRestart.addEventListener('click', async () => {
                const pkg = appSelect ? appSelect.value : '';
                if (!pkg) { alert('Lütfen önce bir uygulama seçin!'); return; }
                btnQuickRestart.disabled = true;
                await postAction('/api/quick_action/restart_app', { app_package: pkg });
                btnQuickRestart.disabled = false;
            });
        }

        if (btnQuickGrant) {
            btnQuickGrant.addEventListener('click', async () => {
                const pkg = appSelect ? appSelect.value : '';
                if (!pkg) { alert('Lütfen önce bir uygulama seçin!'); return; }
                btnQuickGrant.disabled = true;
                await postAction('/api/quick_action/grant_permissions', { app_package: pkg });
                btnQuickGrant.disabled = false;
            });
        }

        // Logcat Live Viewer Modal Logic
        const btnOpenLogcat = document.getElementById('btn-open-logcat');
        const logcatModal = document.getElementById('logcat-modal');
        const btnCloseLogcat = document.getElementById('btn-close-logcat');
        const logcatPackageFilter = document.getElementById('logcat-package-filter');
        const logcatTextFilter = document.getElementById('logcat-text-filter');
        const logcatLinesSelect = document.getElementById('logcat-lines-select');
        const checkLogcatLive = document.getElementById('check-logcat-live');
        const btnRefreshLogcat = document.getElementById('btn-refresh-logcat');
        const btnClearDeviceLogcat = document.getElementById('btn-clear-device-logcat');
        const btnCopyLogcat = document.getElementById('btn-copy-logcat');
        const logcatOutput = document.getElementById('logcat-output');
        const logcatTargetDisplay = document.getElementById('logcat-target-display');
        const logcatCountBadge = document.getElementById('logcat-count-badge');

        let logcatInterval = null;
        let lastLogcatRaw = '';

        if (btnOpenLogcat && logcatModal) {
            btnOpenLogcat.addEventListener('click', () => {
                const currentPkg = appSelect ? appSelect.value : '';
                if (logcatPackageFilter) logcatPackageFilter.value = currentPkg;
                if (logcatTargetDisplay) logcatTargetDisplay.textContent = currentPkg ? `Hedef: ${currentPkg}` : 'Hedef: Tüm Cihaz';
                logcatModal.style.display = 'flex';
                document.querySelector('.app-container').inert = true;
                fetchLogcatData();

                if (checkLogcatLive && checkLogcatLive.checked) {
                    startLogcatLiveStream();
                }
            });
        }

        if (btnCloseLogcat && logcatModal) {
            btnCloseLogcat.addEventListener('click', () => {
                logcatModal.style.display = 'none';
                document.querySelector('.app-container').inert = false;
                stopLogcatLiveStream();
            });
        }

        window.addEventListener('click', (e) => {
            if (logcatModal && e.target === logcatModal) {
                logcatModal.style.display = 'none';
                document.querySelector('.app-container').inert = false;
                stopLogcatLiveStream();
            }
        });

        function startLogcatLiveStream() {
            stopLogcatLiveStream();
            logcatInterval = setInterval(fetchLogcatData, 1200);
        }

        function stopLogcatLiveStream() {
            logcatRequestId++; logcatQueued = false; clearTimeout(logcatDebounce);
            if (logcatInterval) {
                clearInterval(logcatInterval);
                logcatInterval = null;
            }
        }

        if (checkLogcatLive) {
            checkLogcatLive.addEventListener('change', () => {
                if (checkLogcatLive.checked) {
                    startLogcatLiveStream();
                } else {
                    stopLogcatLiveStream();
                }
            });
        }

        if (btnRefreshLogcat) btnRefreshLogcat.addEventListener('click', fetchLogcatData);

        if (btnClearDeviceLogcat) {
            btnClearDeviceLogcat.addEventListener('click', async () => {
                await postAction('/api/clear_logcat');
                if (logcatOutput) logcatOutput.innerHTML = '<div style="opacity: 0.5; font-style: italic;">Logcat temizlendi.</div>';
                if (logcatCountBadge) logcatCountBadge.textContent = '0 satır';
            });
        }

        if (btnCopyLogcat) {
            btnCopyLogcat.addEventListener('click', () => {
                if (lastLogcatRaw) {
                    navigator.clipboard.writeText(lastLogcatRaw);
                    alert('Logcat panoya kopyalandı!');
                }
            });
        }

        if (logcatPackageFilter) {
            logcatPackageFilter.addEventListener('input', () => {
                const val = logcatPackageFilter.value.trim();
                if (logcatTargetDisplay) logcatTargetDisplay.textContent = val ? `Hedef: ${val}` : 'Hedef: Tüm Cihaz';
                scheduleLogcat();
            });
        }

        if (logcatTextFilter) logcatTextFilter.addEventListener('input', scheduleLogcat);
        if (logcatLinesSelect) logcatLinesSelect.addEventListener('change', fetchLogcatData);

        let logcatRequestId = 0, logcatPending = false, logcatQueued = false, logcatDebounce;
        function scheduleLogcat() {
            logcatRequestId++;
            clearTimeout(logcatDebounce);
            logcatDebounce = setTimeout(fetchLogcatData, 180);
        }
        async function fetchLogcatData() {
            if (logcatPending) { logcatQueued = true; return; }
            logcatPending = true;
            const requestId = ++logcatRequestId;
            if (!logcatOutput) return;
            try {
                const pkg = logcatPackageFilter ? logcatPackageFilter.value.trim() : '';
                const filter = logcatTextFilter ? logcatTextFilter.value.trim() : '';
                const lines = logcatLinesSelect ? logcatLinesSelect.value : 250;

                const res = await apiFetch('/api/get_logcat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        app_package: pkg,
                        filter_text: filter,
                        lines: lines
                    })
                });

                const data = await res.json();
                if (requestId !== logcatRequestId) return;
                if (!res.ok || !data.success) {
                    lastLogcatRaw = '';
                    logcatOutput.textContent = data.message || data.error || 'Logcat alınamadı.';
                    if (logcatCountBadge) logcatCountBadge.textContent = '0 satır';
                    return;
                }
                if (data.success && data.logs) {
                    lastLogcatRaw = data.logs.join('\n');
                    if (logcatCountBadge) logcatCountBadge.textContent = `${data.logs.length} satır yüklendi`;

                    if (data.logs.length === 0) {
                        logcatOutput.innerHTML = '<div style="opacity: 0.5; font-style: italic; text-align: center; padding: 30px 0;">Eşleşen log bulunamadı (Uygulama çalışıyor mu?).</div>';
                        return;
                    }

                    const wasAtBottom = logcatOutput.scrollHeight - logcatOutput.clientHeight <= logcatOutput.scrollTop + 50;

                    logcatOutput.innerHTML = '';
                    data.logs.forEach(line => {
                        const div = document.createElement('div');
                        div.className = 'logcat-line';

                        if (line.includes(' E ') || line.includes('FATAL') || line.includes('Exception') || line.includes('Error')) {
                            div.classList.add('err');
                        } else if (line.includes(' W ') || line.includes('WARN')) {
                            div.classList.add('warn');
                        } else if (line.includes(' I ') || line.includes('INFO')) {
                            div.classList.add('info');
                        }

                        div.textContent = line;
                        logcatOutput.appendChild(div);
                    });

                    if (wasAtBottom || (checkLogcatLive && checkLogcatLive.checked)) {
                        logcatOutput.scrollTop = logcatOutput.scrollHeight;
                    }
                }
            } catch (err) {
                logcatOutput.textContent = 'Logcat alınamadı. Yeniden deneyin.';
            } finally {
                logcatPending = false;
                if (logcatQueued && logcatModal.style.display === 'flex') { logcatQueued = false; fetchLogcatData(); }
            }
        }

        // Initialize on load
        window.addEventListener('DOMContentLoaded', async () => {
            initDropdowns();
            initResizableSplitters();

            // Run all loaders independently with error catching so one error never blocks the rest
            updateStatus(); loadPackagesList(); loadScriptsList();
            try { loadRunningPackagesList(); } catch(e) { console.error('loadRunningPackagesList error:', e); }
            try { setupLogStream(); } catch(e) { console.error('setupLogStream error:', e); }

            // Poll status every 2 seconds
            setInterval(() => {
                if (!document.hidden) { updateStatus(); loadRunningPackagesList(); }
            }, 1000);
            setInterval(() => { if (!document.hidden) loadPackagesList(); }, 3000);
        });

scriptEditor.addEventListener('input', () => {
    editorDirty = true;
    const badge = document.getElementById('editor-save-status');
    badge.textContent = 'Kaydedilmedi'; badge.dataset.dirty = 'true';
});
scriptEditor.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveScript(); }
});
window.addEventListener('beforeunload', e => { if (editorDirty) { e.preventDefault(); e.returnValue = ''; } });
// Escape, focus return and Tab containment for the log viewer.
btnOpenLogcat.addEventListener('click', () => { btnCloseLogcat.focus(); });
btnCloseLogcat.addEventListener('click', () => { btnOpenLogcat.focus(); });
document.addEventListener('keydown', e => {
    if (logcatModal.style.display !== 'flex') return;
    if (e.key === 'Escape') { e.preventDefault(); btnCloseLogcat.click(); }
    if (e.key === 'Tab') {
        const nodes = Array.from(logcatModal.querySelectorAll('button, input, select')).filter(el => !el.disabled && el.getClientRects().length);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
});
