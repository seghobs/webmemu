// Instagram SSL Pinning Bypass v5.5 - Frida 17.x
console.log("[!] Instagram SSL Bypass v5.5");

// Wait for app to fully load
Java.perform(function() {
    console.log("[*] Java VM ready");
});

setTimeout(function() {
    Java.perform(function() {
        console.log("\n[*] SSL Bypass başlatılıyor...");

        // TrustManagerImpl - en önemli bypass
        try {
            var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
            
            // Tüm overload'ları dene
            var methods = TrustManagerImpl.verifyChain.overloads;
            methods.forEach(function(method) {
                method.implementation = function() {
                    var host = "";
                    if (arguments.length >= 3 && typeof arguments[2] === 'string') {
                        host = " -> " + arguments[2];
                    }
                    var certSubject = "";
                    try {
                        if (arguments.length > 0 && arguments[0] && arguments[0].length > 0) {
                            certSubject = " [" + arguments[0][0].getSubjectDN().toString() + "]";
                        }
                    } catch(e) {}
                    console.log("[✓] TrustManager verifyChain bypassed" + host + certSubject);
                    return arguments[0]; // untrustedChain döndür
                };
            });
            console.log("[✓] TrustManagerImpl hooked");
        } catch(e) {
            console.log("[-] TrustManagerImpl: " + e);
        }

        // OkHttp3
        try {
            var CertPinner = Java.use('okhttp3.CertificatePinner');
            CertPinner.check.overload('java.lang.String', 'java.util.List').implementation = function(h, c) {
                console.log("[✓] OkHttp3 CertificatePinner bypassed -> " + h);
            };
            console.log("[✓] OkHttp3 hooked");
        } catch(e) {
            console.log("[-] OkHttp3: " + e);
        }

        // SSLContext
        try {
            var SSLContext = Java.use('javax.net.ssl.SSLContext');
            SSLContext.init.overload('[Ljavax.net.ssl.KeyManager;', '[Ljavax.net.ssl.TrustManager;', 'java.security.SecureRandom').implementation = function(a, b, c) {
                console.log("[✓] SSLContext.init bypassed (Custom TrustManager injected)");
                this.init(a, null, c);
            };
            console.log("[✓] SSLContext hooked");
        } catch(e) {
            console.log("[-] SSLContext: " + e);
        }

        console.log("\n[✓] Bypass tamamlandı!");
    });
}, 1000);

// Facebook specific - daha geç yükle
setTimeout(function() {
    Java.perform(function() {
        try {
            var fb = Java.use('com.facebook.mobilenetwork.internal.certificateverifier.CertificateVerifier');
            fb.verify.overloads.forEach(function(m) {
                m.implementation = function() {
                    console.log("[✓] Facebook CertVerifier bypassed");
                    var retType = m.returnType.className || m.returnType.name || '';
                    if (retType === 'void') {
                        return;
                    }
                    return true;
                };
            });
            console.log("[✓] Facebook CertVerifier hooked");
        } catch(e) {
            console.log("[-] Facebook: " + e);
        }
    });
}, 5000);

console.log("[*] Script yüklendi, bekleyin...");
