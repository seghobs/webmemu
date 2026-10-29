// Observation only: original response methods run normally; no traffic bodies are recorded.
Java.perform(function () {
    var Factory = Java.use('java.security.cert.CertificateFactory');
    var Stream = Java.use('java.io.ByteArrayInputStream');
    var Base64 = Java.use('android.util.Base64');
    var root = Factory.getInstance('X.509').generateCertificate(Stream.$new(Base64.decode(SMART_CA, 0)));
    var hooks = 0, seen = {};
    function emit(value) { console.log('SMART_EVENT:' + SMART_NONCE + ':' + JSON.stringify(value)); }
    function evidence(host, code, certificate) {
        try {
            certificate.verify(root.getPublicKey());
            host = String(host);
            if (!seen[host] && Object.keys(seen).length < 32) {
                seen[host] = true;
                emit({kind:'https_response', host:host, code:Number(code)});
            }
        } catch (_) {} // A response not signed by our CA is not evidence of interception.
    }
    ['com.android.okhttp.internal.huc.HttpsURLConnectionImpl',
     'com.android.okhttp.internal.huc.HttpsURLConnectionImpl$DelegatingHttpsURLConnection'].forEach(function (name) {
        try {
            var method = Java.use(name).getResponseCode.overload();
            method.implementation = function () {
                var code = method.call(this);
                try { evidence(this.getURL().getHost(), code, this.getServerCertificates()[0]); } catch (_) {}
                return code;
            };
            hooks++;
        } catch (_) {}
    });
    ['okhttp3.RealCall', 'okhttp3.internal.connection.RealCall'].forEach(function (name) {
        ['getResponseWithInterceptorChain', 'getResponseWithInterceptorChain$okhttp'].forEach(function (methodName) {
            try {
                var method = Java.use(name)[methodName].overload();
                method.implementation = function () {
                    var response = method.call(this);
                    try {
                        var handshake = response.handshake();
                        if (handshake && response.request().url().isHttps() && response.networkResponse()) {
                            var cert = Java.cast(handshake.peerCertificates().get(0), Java.use('java.security.cert.X509Certificate'));
                            evidence(response.request().url().host(), response.code(), cert);
                        }
                    } catch (_) {}
                    return response;
                };
                hooks++;
            } catch (_) {}
        });
    });
    emit({kind:'observer_ready', hooks:hooks});
});
