/**
 * Flutter SSL Pinning Bypass
 *
 * Flutter uygulamaları standart Android TrustManager yerine kendi derlenmiş
 * libflutter.so kütüphanesini ve BoringSSL motorunu kullanır.
 * Bu script, libflutter.so içindeki ssl_verify_peer_cert / session_verify_cert_chain
 * fonksiyonlarını bellekte pattern-matching veya exported semboller ile bularak bypass eder.
 */

console.log("[*] Flutter SSL Pinning Bypass scripti yüklendi.");

Java.perform(function() {
    console.log("[*] Java katmanı hazır, libflutter.so taranıyor...");
});

// Native Hooking for libflutter.so
function hookFlutter() {
    var targetModule = Process.findModuleByName("libflutter.so");
    if (!targetModule) {
        // Modül henüz yüklenmediyse tekrar dene
        setTimeout(hookFlutter, 500);
        return;
    }

    console.log("[✓] libflutter.so bulundu: Base=" + targetModule.base + " Size=" + targetModule.size);

    var patterns = {
        "arm64": [
            "F? 0F 1C F8 F? 57 01 A9 F? 5F 02 A9 F? 7? 0? A9",
            "FF 03 01 D1 F8 5F 01 A9 F6 57 02 A9 F4 4F 03 A9 FD 7B 04 A9 FD 03 01 91 F3 03 00 AA 1F 01 09 6B",
            "FF 43 01 D1 F8 5F 01 A9 F6 57 02 A9 F4 4F 03 A9 FD 7B 04 A9 FD 03 01 91 F3 03 00 AA"
        ],
        "arm": [
            "2D E9 F0 4F A3 B0 8? 46 9? 46",
            "2D E9 F0 4F A3 B0",
            "82 B0 ?0 90 ?0 91"
        ],
        "x64": [
            "55 48 89 E5 41 57 41 56 41 55 41 54 53 48 83 EC ? 48 89 7D ?",
            "55 48 89 E5 41 56 41 55 41 54 53 48 83 EC",
            "55 48 89 E5 53 48 83 EC"
        ],
        "ia32": [
            "55 89 E5 57 56 53 83 EC",
            "55 89 E5 83 EC"
        ]
    };

    var arch = Process.arch;
    var archPatterns = patterns[arch] || patterns["x64"] || patterns["arm64"];
    var hooked = false;

    archPatterns.forEach(function(pattern) {
        if (hooked) return;
        try {
            Memory.scan(targetModule.base, targetModule.size, pattern, {
                onMatch: function(address, size) {
                    console.log("[✓] Flutter SSL doğrulama fonksiyonu tespit edildi: " + address);
                    try {
                        Interceptor.attach(address, {
                            onLeave: function(retval) {
                                console.log("[✓] Flutter ssl_verify_peer_cert çağrıldı ➔ Bypassed!");
                                retval.replace(ptr(0x1)); // TRUE / SUCCESS
                            }
                        });
                        hooked = true;
                    } catch(err) {
                        console.log("[*] Interceptor hatası: " + err);
                    }
                },
                onError: function(reason) {
                    // Tarama hatası
                },
                onComplete: function() {
                    if (hooked) {
                        console.log("[✓] Flutter SSL Bypass başarıyla tamamlandı!");
                    }
                }
            });
        } catch(e) {
            // Pattern tarama hatası
        }
    });

    // Fallback: Java TrustManager
    Java.perform(function() {
        try {
            var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
            TrustManagerImpl.verifyChain.implementation = function() {
                console.log("[✓] Flutter/Conscrypt TrustManager verifyChain bypassed");
                return arguments[0];
            };
        } catch(e) {}
    });
}

// Başlat
setTimeout(hookFlutter, 1000);
