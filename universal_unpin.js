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
