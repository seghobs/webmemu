/**
 * headers.js - Instagram Request Header Interceptor
 * 
 * Bu script, Instagram'ın giden ağ isteklerini (OkHttp3/Liger ve HttpURLConnection)
 * dinler ve özellikle "x-meta-zca" vb. başlıkları yakalayarak konsola yazar.
 */

console.log("[*] headers.js scripti yüklendi. İstekler dinleniyor...");

Java.perform(function () {
    try {
        // 1. OkHttp3 Request Hook
        var RequestBuilder = Java.use("okhttp3.Request$Builder");
        
        // OkHttp3 addHeader metodu hooklanır
        RequestBuilder.addHeader.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
            if (name.toLowerCase() === "x-meta-zca" || name.toLowerCase().includes("attest")) {
                console.log("🟢 [OkHttp3 AddHeader] " + name + ": " + value);
                try {
                    // Base64 decode etmeyi dene (x-meta-zca genellikle base64 json formatındadır)
                    var decoded = decodeBase64(value);
                    if (decoded) {
                        console.log("   └─ [Deşifre Edilen Veri]: " + decoded);
                    }
                } catch(e) {}
            } else if (name.toLowerCase().startsWith("x-ig-") || name.toLowerCase().startsWith("x-meta-")) {
                console.log("⚪ [OkHttp3 Meta Header] " + name + ": " + value);
            }
            return this.addHeader(name, value);
        };

        // OkHttp3 header metodu hooklanır
        RequestBuilder.header.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
            if (name.toLowerCase() === "x-meta-zca" || name.toLowerCase().includes("attest")) {
                console.log("🟢 [OkHttp3 Header] " + name + ": " + value);
                try {
                    var decoded = decodeBase64(value);
                    if (decoded) {
                        console.log("   └─ [Deşifre Edilen Veri]: " + decoded);
                    }
                } catch(e) {}
            }
            return this.header(name, value);
        };

    } catch (e) {
        console.log("[*] OkHttp3 kütüphanesi bu sürümde bulunamadı (Atlandı).");
    }

    try {
        // 2. HttpURLConnection SetRequestProperty Hook
        var HttpURLConnection = Java.use("java.net.HttpURLConnection");
        HttpURLConnection.setRequestProperty.overload('java.lang.String', 'java.lang.String').implementation = function (key, value) {
            if (key.toLowerCase() === "x-meta-zca" || key.toLowerCase().includes("attest")) {
                console.log("🟢 [URLConnection Property] " + key + ": " + value);
                try {
                    var decoded = decodeBase64(value);
                    if (decoded) {
                        console.log("   └─ [Deşifre Edilen Veri]: " + decoded);
                    }
                } catch(e) {}
            }
            this.setRequestProperty(key, value);
        };
    } catch (e) {
        console.log("[-] java.net.HttpURLConnection hooklanamadı: " + e);
    }

    try {
        // 3. Instagram Liger / Proxygen HTTP Engine Hook (Metanın kendi native ağ kütüphanesi)
        // Liger, giden isteklerde header eklerken bu sınıfları veya sarmalayıcıları (wrapper) kullanır.
        var LigerRequest = Java.use("com.facebook.proxygen.HTTPRequestHandler");
        LigerRequest.executeWithBody.implementation = function (a, b, c, d, e, f, g) {
            console.log("🔵 [Liger HTTP Engine Request Triggered]");
            return this.executeWithBody(a, b, c, d, e, f, g);
        };
    } catch(e) {
        // Obfuscated class handle or native fallback silently
    }

    try {
        // Instagram'ın kendi internal Request/Header Builder Yapılarını Hooklayalım
        var IGRequestFactory = Java.use("com.instagram.common.api.base.HttpRequest");
        IGRequestFactory.getHeaders.implementation = function () {
            var headersList = this.getHeaders();
            if (headersList) {
                // Header dizisini veya listesini oku
                for (var i = 0; i < headersList.size(); i++) {
                    var headerObj = headersList.get(i);
                    var name = headerObj.getName();
                    var value = headerObj.getValue();
                    if (name.toLowerCase() === "x-meta-zca" || name.toLowerCase().includes("attest")) {
                        console.log("🟢 [IG API Request] " + name + ": " + value);
                        var decoded = decodeBase64(value);
                        if (decoded) {
                            console.log("   └─ [Deşifre Edilen Veri]: " + decoded);
                        }
                    } else if (name.toLowerCase().startsWith("x-ig-") || name.toLowerCase().startsWith("x-meta-")) {
                        console.log("⚪ [IG API Header] " + name + ": " + value);
                    }
                }
            }
            return headersList;
        };
    } catch(e) {
        // Obfuscation fallback
    }

    // Yardımcı Base64 çözücü
    function decodeBase64(input) {
        try {
            var Base64 = Java.use("android.util.Base64");
            var StringClass = Java.use("java.lang.String");
            var decodedBytes = Base64.decode(input, 0); // 0 = Base64.DEFAULT
            return StringClass.$new(decodedBytes);
        } catch (e) {
            return null;
        }
    }
});
