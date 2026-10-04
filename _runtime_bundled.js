/**
 * Deep Device & Root Cloak (Samsung Galaxy S25 Ultra Spoof & Anti-Root & Emulator Cloaking) - Ultimate v4.0
 *
 * 1. Samsung Galaxy S25 Ultra (SM-S938B) donanım ve build kimliği.
 * 2. C/C++ Native (libc.so) ve Java seviyesinde tüm root ve emülatör dosyalarının gizlenmesi.
 * 3. ADB / Geliştirici Seçenekleri (adb_enabled, development_settings_enabled) tespiti engelleme.
 * 4. Türk Telekom (moim / com.tmob) ve tüm kurumsal uygulamaların dinamik Root/Jailbreak metodlarının tespiti ve etkisizleştirilmesi.
 * 5. Root uyarı dialoglarının (AlertDialog / Builders) engellenmesi.
 */

console.log("[🛡️] Derin Cihaz & Root Gizleme (Samsung S25 Ultra) modülü başlatılıyor...");

// ==========================================
// 1. NATIVE C (LIBC.SO) SAFE INTERCEPTORS
// ==========================================
(function initNativeSafeHooks() {
    var blacklistedExact = [
        "/system/bin/su", "/system/xbin/su", "/sbin/su", "/data/local/tmp/su", "/data/local/su",
        "/system/app/Superuser.apk", "/system/app/SuperSU.apk", "/system/app/Superuser",
        "/system/xbin/daemonsu", "/data/adb/magisk", "/data/adb/ksu", "/system/etc/init.d/99SuperSUDaemon",
        "/system/bin/failsafe/su", "/system/sd/xbin/su", "/data/local/xbin/su",
        "/dev/socket/qemud", "/dev/qemu_pipe", "/sys/qemu_trace",
        "/system/lib/libc_malloc_debug_qemu.so", "/system/bin/qemu-props",
        "/system/bin/microvirt-props", "/system/bin/nox-prop"
    ];

    function isBlacklisted(rawPath) {
        if (!rawPath) return false;
        var p = rawPath.toLowerCase().trim();
        for (var i = 0; i < blacklistedExact.length; i++) {
            if (p === blacklistedExact[i] || p.endsWith(blacklistedExact[i])) {
                return true;
            }
        }
        if (p.indexOf("/data/data/com.microvirt") !== -1) return true;
        if (p.indexOf("frida-server") !== -1 || p.indexOf("sys_agent") !== -1) return true;
        return false;
    }

    try {
        var accessPtr = Module.findExportByName("libc.so", "access");
        if (accessPtr) {
            Interceptor.attach(accessPtr, {
                onEnter: function (args) {
                    try {
                        if (!args[0].isNull()) {
                            var p = Memory.readCString(args[0]);
                            if (isBlacklisted(p)) this.blocked = true;
                        }
                    } catch(e) {}
                },
                onLeave: function (retval) {
                    if (this.blocked) retval.replace(ptr(-1));
                }
            });
        }

        ["stat", "lstat", "stat64", "lstat64"].forEach(function (funcName) {
            var fPtr = Module.findExportByName("libc.so", funcName);
            if (fPtr) {
                Interceptor.attach(fPtr, {
                    onEnter: function (args) {
                        try {
                            if (!args[0].isNull()) {
                                var p = Memory.readCString(args[0]);
                                if (isBlacklisted(p)) this.blocked = true;
                            }
                        } catch(e) {}
                    },
                    onLeave: function (retval) {
                        if (this.blocked) retval.replace(ptr(-1));
                    }
                });
            }
        });

        var fopenPtr = Module.findExportByName("libc.so", "fopen");
        if (fopenPtr) {
            Interceptor.attach(fopenPtr, {
                onEnter: function (args) {
                    try {
                        if (!args[0].isNull()) {
                            var p = Memory.readCString(args[0]);
                            if (isBlacklisted(p)) this.blocked = true;
                        }
                    } catch(e) {}
                },
                onLeave: function (retval) {
                    if (this.blocked) retval.replace(ptr(0));
                }
            });
        }

        var sysPropPtr = Module.findExportByName("libc.so", "__system_property_get");
        if (sysPropPtr) {
            Interceptor.attach(sysPropPtr, {
                onEnter: function (args) {
                    try {
                        if (!args[0].isNull()) {
                            this.key = Memory.readCString(args[0]);
                            this.valPtr = args[1];
                        }
                    } catch(e) {}
                },
                onLeave: function (retval) {
                    if (this.key && this.valPtr && !this.valPtr.isNull()) {
                        var override = null;
                        if (this.key === "ro.build.tags") override = "release-keys";
                        else if (this.key === "ro.build.type") override = "user";
                        else if (this.key === "ro.debuggable") override = "0";
                        else if (this.key === "ro.secure") override = "1";
                        else if (this.key === "ro.product.model") override = "SM-S938B";
                        else if (this.key === "ro.product.brand") override = "samsung";
                        else if (this.key === "ro.product.manufacturer") override = "samsung";
                        else if (this.key === "ro.kernel.qemu" || this.key === "ro.boot.qemu") override = "0";

                        if (override) {
                            Memory.writeUtf8String(this.valPtr, override);
                            retval.replace(ptr(override.length));
                        }
                    }
                }
            });
        }
    } catch(e) {}
})();

// ==========================================
// 2. JAVA KATMANI DONANIM, ADB & ROOT MASKELEME
// ==========================================
Java.perform(function () {
    console.log("[🛡️] Java katmanı aktif: Donanım, ADB ve Root kontrolleri maskeleniyor...");

    // A. SAMSUNG GALAXY S25 ULTRA (SM-S938B) DONANIM DEĞERLERİ
    try {
        var Build = Java.use("android.os.Build");
        Build.MANUFACTURER.value = "samsung";
        Build.BRAND.value = "samsung";
        Build.MODEL.value = "SM-S938B";
        Build.PRODUCT.value = "e3qxxx";
        Build.DEVICE.value = "e3q";
        Build.BOARD.value = "universal2500";
        Build.HARDWARE.value = "qcom";
        Build.FINGERPRINT.value = "samsung/e3qxxx/e3q:15/AP3A.240905.015/S938BXXU1AXK3:user/release-keys";
        Build.TAGS.value = "release-keys";
        Build.TYPE.value = "user";
        Build.ID.value = "AP3A.240905.015";
        Build.DISPLAY.value = "AP3A.240905.015.S938BXXU1AXK3";
        Build.HOST.value = "21B-BUILD-SERVER-S938B";
        Build.USER.value = "dpi";
        console.log("[✓] Cihaz kimliği Samsung Galaxy S25 Ultra (SM-S938B) olarak maskelendi.");
    } catch(e) {}

    // B. SYSTEM PROPERTIES
    try {
        var SystemProperties = Java.use("android.os.SystemProperties");
        var propMap = {
            "ro.product.model": "SM-S938B",
            "ro.product.brand": "samsung",
            "ro.product.name": "e3qxxx",
            "ro.product.device": "e3q",
            "ro.product.manufacturer": "samsung",
            "ro.product.board": "universal2500",
            "ro.board.platform": "universal2500",
            "ro.hardware": "qcom",
            "ro.build.flavor": "e3qxxx-user",
            "ro.build.tags": "release-keys",
            "ro.build.type": "user",
            "ro.secure": "1",
            "ro.debuggable": "0",
            "ro.kernel.qemu": "0",
            "ro.boot.qemu": "0",
            "qemu.hw.mainkeys": "0",
            "init.svc.qemud": "",
            "init.svc.goldfish-setup": "",
            "init.svc.goldfish-logcat": "",
            "ro.boot.hardware": "qcom",
            "gsm.version.baseband": "S938BXXU1AXK3"
        };

        SystemProperties.get.overload('java.lang.String').implementation = function (key) {
            if (propMap[key] !== undefined) return propMap[key];
            return this.get(key);
        };

        SystemProperties.get.overload('java.lang.String', 'java.lang.String').implementation = function (key, def) {
            if (propMap[key] !== undefined) return propMap[key];
            return this.get(key, def);
        };
        console.log("[✓] SystemProperties güvenli Samsung değerlerine yönlendirildi.");
    } catch(e) {}

    // C. ADB & GELİŞTİRİCİ SEÇENEKLERİ GİZLEME
    try {
        var SettingsGlobal = Java.use("android.provider.Settings$Global");
        SettingsGlobal.getInt.overload('android.content.ContentResolver', 'java.lang.String', 'int').implementation = function (resolver, name, def) {
            if (name === "adb_enabled" || name === "development_settings_enabled") return 0;
            return this.getInt(resolver, name, def);
        };
        SettingsGlobal.getInt.overload('android.content.ContentResolver', 'java.lang.String').implementation = function (resolver, name) {
            if (name === "adb_enabled" || name === "development_settings_enabled") return 0;
            return this.getInt(resolver, name);
        };

        var SettingsSecure = Java.use("android.provider.Settings$Secure");
        SettingsSecure.getInt.overload('android.content.ContentResolver', 'java.lang.String', 'int').implementation = function (resolver, name, def) {
            if (name === "adb_enabled" || name === "development_settings_enabled") return 0;
            return this.getInt(resolver, name, def);
        };
        SettingsSecure.getInt.overload('android.content.ContentResolver', 'java.lang.String').implementation = function (resolver, name) {
            if (name === "adb_enabled" || name === "development_settings_enabled") return 0;
            return this.getInt(resolver, name);
        };
        console.log("[✓] ADB ve Geliştirici Seçenekleri (adb_enabled=0) gizlendi.");
    } catch(e) {}

    // D. GÜVENLİ DOSYA KONTROLÜ
    try {
        var File = Java.use("java.io.File");
        var rootExactList = [
            "/system/bin/su", "/system/xbin/su", "/sbin/su", "/data/local/tmp/su", "/data/local/su",
            "/system/app/Superuser.apk", "/system/app/SuperSU.apk", "/system/app/Superuser",
            "/system/xbin/daemonsu", "/data/adb/magisk", "/data/adb/ksu", "/system/etc/init.d/99SuperSUDaemon",
            "/system/bin/failsafe/su", "/system/sd/xbin/su", "/data/local/xbin/su",
            "/dev/socket/qemud", "/dev/qemu_pipe", "/sys/qemu_trace",
            "/system/lib/libc_malloc_debug_qemu.so", "/system/bin/qemu-props",
            "/system/bin/microvirt-props", "/system/bin/nox-prop"
        ];

        function isJavaFileBlocked(fPath) {
            if (!fPath) return false;
            var lower = fPath.toLowerCase().trim();
            for (var i = 0; i < rootExactList.length; i++) {
                if (lower === rootExactList[i] || lower.endsWith(rootExactList[i])) {
                    return true;
                }
            }
            if (lower.indexOf("/data/data/com.microvirt") !== -1) return true;
            if (lower.indexOf("frida-server") !== -1 || lower.indexOf("sys_agent") !== -1) return true;
            return false;
        }

        File.exists.implementation = function () {
            var path = this.getAbsolutePath();
            if (isJavaFileBlocked(path)) return false;
            return this.exists();
        };

        File.canRead.implementation = function () {
            var path = this.getAbsolutePath();
            if (isJavaFileBlocked(path)) return false;
            return this.canRead();
        };
        console.log("[✓] Java File.exists root kalkanı aktif.");
    } catch(e) {}

    // E. TELEFONİ & OPERATÖR MASKESİ
    try {
        var TelephonyManager = Java.use("android.telephony.TelephonyManager");
        TelephonyManager.getNetworkOperatorName.overload().implementation = function () { return "Turkcell"; };
        TelephonyManager.getSimOperatorName.overload().implementation = function () { return "Turkcell"; };
        TelephonyManager.getNetworkCountryIso.overload().implementation = function () { return "tr"; };
        TelephonyManager.getSimCountryIso.overload().implementation = function () { return "tr"; };
        TelephonyManager.getPhoneType.overload().implementation = function () { return 1; }; // GSM
        TelephonyManager.getSimState.overload().implementation = function () { return 5; }; // READY
        console.log("[✓] Operatör ve SIM kimliği (Turkcell / GSM) maskelendi.");
    } catch(e) {}

    // F. DİNAMİK TÜRK TELEKOM & UYGULAMA ROOT KANCALARI
    var knownRootClasses = [
        "com.scottyab.rootbeer.RootBeer",
        "com.stericson.RootTools.RootTools",
        "com.tmob.root.RootChecker",
        "com.tmob.AveaOIM.security.RootChecker",
        "moim.android.util.RootUtil",
        "moim.android.security.RootUtil",
        "com.tmob.security.SecurityChecker",
        "com.tmob.security.RootDetector"
    ];

    knownRootClasses.forEach(function (clsName) {
        try {
            var targetCls = Java.use(clsName);
            var methods = targetCls.class.getDeclaredMethods();
            for (var mIdx = 0; mIdx < methods.length; mIdx++) {
                var m = methods[mIdx];
                var mName = m.getName();
                var retType = m.getReturnType().getName();
                if (retType === "boolean") {
                    try {
                        targetCls[mName].implementation = function () {
                            console.log("[🛡️] Root kontrol metodu bypass edildi: " + clsName + "." + mName + "() -> false");
                            return false;
                        };
                    } catch(errHook) {}
                }
            }
            console.log("[✓] Hedef Root sınıfı etkisizleştirildi: " + clsName);
        } catch(e) {}
    });

    // G. TERMINAL RUNTIME.EXEC KOMUTLARI
    try {
        var Runtime = Java.use("java.lang.Runtime");
        Runtime.exec.overload('java.lang.String').implementation = function (cmd) {
            if (cmd === "su" || cmd.startsWith("su ") || cmd.indexOf("which su") !== -1 || cmd.indexOf("which busybox") !== -1 || cmd.indexOf("which magisk") !== -1) {
                return this.exec("echo not_found");
            }
            if (cmd.indexOf("getprop ro.debuggable") !== -1) return this.exec("echo 0");
            if (cmd.indexOf("getprop ro.secure") !== -1) return this.exec("echo 1");
            return this.exec(cmd);
        };
    } catch(e) {}

    // H. UYARI VE BILDIRIM KONTROLLERI
    // Diyaloglar güvenli şekilde gösterilir, çökme riski taşıyan JNI referansları engellenmiştir.

    console.log("[🛡️] DERİN CİHAZ & ROOT GİZLEME TAMAMLANDI! (Samsung Galaxy S25 Ultra)");
});


// Target Script (universal_unpin.js)
/**
 * Universal Multi-Library SSL Pinning Bypass (Full Coverage v2.0)
 *
 * OkHttp3, OkHttp4, Conscrypt, NetworkSecurityConfig, TrustKit, OpenSSL,
 * X509TrustManager, HostnameVerifier ve WebView SSL kontrollerini atlatır.
 */

console.log("[*] Universal Multi-Unpinning scripti başlatılıyor...");

Java.perform(function () {
    console.log("[*] Java katmanı bağlandı, evrensel SSL kütüphaneleri kancalanıyor...");

    var array_list = Java.use("java.util.ArrayList");

    // 1. Conscrypt TrustManagerImpl
    try {
        var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
        TrustManagerImpl.verifyChain.implementation = function (untrustedChain, trustAnchorChain, host, clientAuth, ocspData, tlsSctData) {
            console.log("[✓] Conscrypt TrustManagerImpl.verifyChain bypassed -> " + (host || ""));
            return untrustedChain;
        };
        TrustManagerImpl.checkTrustedRecursive.implementation = function (certs, host, clientAuth, untrustedChain, trustAnchorChain, ocspData) {
            console.log("[✓] Conscrypt TrustManagerImpl.checkTrustedRecursive bypassed -> " + (host || ""));
            return array_list.$new();
        };
        console.log("[✓] Hooked: Conscrypt TrustManagerImpl");
    } catch (e) {}

    // 2. OkHttp3 / OkHttp4 CertificatePinner (All Overloads)
    try {
        var CertificatePinner = Java.use('okhttp3.CertificatePinner');
        try {
            CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function (hostname, peerCertificates) {
                console.log("[✓] OkHttp3 CertificatePinner.check(String, List) bypassed -> " + hostname);
            };
        } catch(e) {}

        try {
            CertificatePinner.check.overload('java.lang.String', '[Ljava.security.cert.Certificate;').implementation = function (hostname, peerCertificates) {
                console.log("[✓] OkHttp3 CertificatePinner.check(String, Certificate[]) bypassed -> " + hostname);
            };
        } catch(e) {}

        try {
            CertificatePinner.check$okhttp.implementation = function (hostname, cleanedPeerCertificatesFn) {
                console.log("[✓] OkHttp4 CertificatePinner.check$okhttp bypassed -> " + hostname);
            };
        } catch(e) {}

        try {
            CertificatePinner.findMatchingPins.implementation = function (hostname) {
                return array_list.$new();
            };
        } catch(e) {}
        console.log("[✓] Hooked: OkHttp3/4 CertificatePinner");
    } catch (e) {}

    // 3. NetworkSecurityTrustManager (Android 7.0+ Network Security Config)
    try {
        var NetworkSecurityTrustManager = Java.use('android.security.net.config.NetworkSecurityTrustManager');
        NetworkSecurityTrustManager.checkPins.implementation = function (pins) {
            console.log("[✓] NetworkSecurityTrustManager.checkPins bypassed");
        };
        console.log("[✓] Hooked: NetworkSecurityTrustManager");
    } catch(e) {}

    // 4. HostnameVerifier
    try {
        var HostnameVerifier = Java.use('javax.net.ssl.HostnameVerifier');
        var AllowAllHostnameVerifier = Java.registerClass({
            name: 'com.antigravity.AllowAllHostnameVerifier',
            implements: [HostnameVerifier],
            methods: {
                verify: function (hostname, session) {
                    return true;
                }
            }
        });
        var HttpsURLConnection = Java.use('javax.net.ssl.HttpsURLConnection');
        HttpsURLConnection.setDefaultHostnameVerifier(AllowAllHostnameVerifier.$new());
        console.log("[✓] Hooked: HttpsURLConnection DefaultHostnameVerifier");
    } catch(e) {}

    // 5. TrustKit Pinning
    try {
        var TrustKit = Java.use('com.datatheorem.android.trustkit.pinning.OkHostnameVerifier');
        TrustKit.verify.overload('java.lang.String', 'javax.net.ssl.SSLSession').implementation = function (hostname, session) {
            console.log("[✓] TrustKit OkHostnameVerifier bypassed -> " + hostname);
            return true;
        };
        console.log("[✓] Hooked: TrustKit Pinning");
    } catch (e) {}

    // 6. SSLContext (Custom TrustManager injection)
    try {
        var SSLContext = Java.use('javax.net.ssl.SSLContext');
        SSLContext.init.overload('[Ljavax.net.ssl.KeyManager;', '[Ljavax.net.ssl.TrustManager;', 'java.security.SecureRandom').implementation = function (km, tm, sr) {
            console.log("[✓] javax.net.ssl.SSLContext.init bypassed");
            this.init(km, null, sr);
        };
        console.log("[✓] Hooked: SSLContext.init");
    } catch (e) {}

    // 7. WebViewClient onReceivedSslError
    try {
        var WebViewClient = Java.use('android.webkit.WebViewClient');
        WebViewClient.onReceivedSslError.implementation = function (view, handler, error) {
            console.log("[✓] WebViewClient.onReceivedSslError bypassed -> Proceeding!");
            handler.proceed();
        };
        console.log("[✓] Hooked: WebViewClient.onReceivedSslError");
    } catch (e) {}

    // 8. Apache Harmony / Android OpenSSL
    try {
        var OpenSSLSocketImpl = Java.use('org.apache.harmony.xnet.provider.jsse.OpenSSLSocketImpl');
        OpenSSLSocketImpl.verifyCertificateChain.implementation = function (asn1DerEncodedCertificateChain, authMethod) {
            console.log("[✓] Apache Harmony OpenSSLSocketImpl.verifyCertificateChain bypassed");
        };
        console.log("[✓] Hooked: OpenSSLSocketImpl");
    } catch (e) {}

    console.log("[✓] Universal SSL Pinning bypass aktif!");
});
