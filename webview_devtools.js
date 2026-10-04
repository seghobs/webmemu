/**
 * WebView Remote Debugging (DevTools) Enabler
 *
 * Bu script, uygulama içindeki gömülü web tarayıcılarında (Android WebView)
 * remote debugging özelliğini kalıcı olarak aktif eder.
 * Böylece Google Chrome'da "chrome://inspect" adresini açarak WebView sayfalarını
 * (HTML, JS, CSS, Network, LocalStorage, Console) canlı olarak inceleyebilirsiniz.
 */

console.log("[*] WebView DevTools Enabler scripti yüklendi.");

Java.perform(function () {
    console.log("[*] Java katmanı hazır, WebView sınıfları hooklanıyor...");

    try {
        var WebView = Java.use("android.webkit.WebView");

        // 1. setWebContentsDebuggingEnabled metodunu her zaman 'true' yap
        WebView.setWebContentsDebuggingEnabled.implementation = function (enabled) {
            console.log("[✓] WebView.setWebContentsDebuggingEnabled(true) zorlandı!");
            this.setWebContentsDebuggingEnabled(true);
        };

        // 2. Her yeni WebView oluşturulduğunda otomatik olarak Remote Debugging'i aç
        WebView.$init.overload('android.content.Context').implementation = function (ctx) {
            this.$init(ctx);
            WebView.setWebContentsDebuggingEnabled(true);
            console.log("[✓] Yeni WebView örneği yakalandı -> Remote Debugging açıldı!");
        };

        WebView.$init.overload('android.content.Context', 'android.util.AttributeSet').implementation = function (ctx, attrs) {
            this.$init(ctx, attrs);
            WebView.setWebContentsDebuggingEnabled(true);
            console.log("[✓] Yeni WebView örneği (AttributeSet) -> Remote Debugging açıldı!");
        };

        WebView.$init.overload('android.content.Context', 'android.util.AttributeSet', 'int').implementation = function (ctx, attrs, defStyle) {
            this.$init(ctx, attrs, defStyle);
            WebView.setWebContentsDebuggingEnabled(true);
            console.log("[✓] Yeni WebView örneği -> Remote Debugging açıldı!");
        };

        // 3. Yüklenen URL'leri izle
        WebView.loadUrl.overload('java.lang.String').implementation = function (url) {
            console.log("🌐 [WebView LoadURL]: " + url);
            this.loadUrl(url);
        };

        console.log("[✓] WebView DevTools aktif! Chrome tarayıcınızda 'chrome://inspect' sekmesini açabilirsiniz.");
    } catch (e) {
        console.log("[-] WebView hooklama hatası: " + e);
    }
});
