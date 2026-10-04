/**
 * Anti-Root & Anti-Debug Detection Bypass
 *
 * Bu script, uygulamaların Root erişimini (RootBeer, su binary, Superuser vb.)
 * ve Debug / Hata Ayıklama modunu tespit eden kontrollerini atlatır.
 */

console.log("[*] Anti-Root & Anti-Debug Bypass scripti yüklendi.");

Java.perform(function () {
    console.log("[*] Java katmanı hazır, Root & Debug hookları tanımlanıyor...");

    // 1. RootBeer Kütüphanesi Bypass
    try {
        var RootBeer = Java.use("com.scottyab.rootbeer.RootBeer");
        RootBeer.isRooted.implementation = function () {
            console.log("[✓] RootBeer.isRooted() çağrıldı ➔ false");
            return false;
        };
        RootBeer.isRootedWithoutBusyBoxCheck.implementation = function () {
            console.log("[✓] RootBeer.isRootedWithoutBusyBoxCheck() ➔ false");
            return false;
        };
        RootBeer.isRootedWithBusyBoxCheck.implementation = function () {
            console.log("[✓] RootBeer.isRootedWithBusyBoxCheck() ➔ false");
            return false;
        };
        RootBeer.checkSuExists.implementation = function () { return false; };
        RootBeer.checkForRootNative.implementation = function () { return false; };
        RootBeer.checkForDangerousProps.implementation = function () { return false; };
        RootBeer.checkForRWPaths.implementation = function () { return false; };
        console.log("[✓] RootBeer kütüphanesi bypass edildi.");
    } catch (e) {}

    // 2. java.io.File Checks (/system/bin/su, Magisk, Superuser.apk vb.)
    try {
        var File = Java.use("java.io.File");
        var StringClass = Java.use("java.lang.String");

        File.exists.implementation = function () {
            var path = this.getAbsolutePath();
            var dangerousPaths = [
                "su", "busybox", "Superuser", "magisk", "daemonsu",
                "/system/xbin/su", "/system/bin/su", "/sbin/su",
                "/data/local/tmp/su", "/system/app/Superuser.apk"
            ];

            for (var i = 0; i < dangerousPaths.length; i++) {
                if (path.indexOf(dangerousPaths[i]) !== -1) {
                    console.log("[✓] File.exists() engellendi (Root tespiti): " + path);
                    return false;
                }
            }
            return this.exists();
        };
        console.log("[✓] Root dosya yolları taraması bypass edildi.");
    } catch (e) {}

    // 3. android.os.Build.TAGS (test-keys -> release-keys)
    try {
        var Build = Java.use("android.os.Build");
        Build.TAGS.value = "release-keys";
        Build.TYPE.value = "user";
        console.log("[✓] Build.TAGS = 'release-keys' ve Build.TYPE = 'user' olarak maskelendi.");
    } catch (e) {}

    // 4. android.os.Debug Detection
    try {
        var Debug = Java.use("android.os.Debug");
        Debug.isDebuggerConnected.implementation = function () {
            console.log("[✓] Debug.isDebuggerConnected() çağrıldı ➔ false");
            return false;
        };
        Debug.waitingForDebugger.implementation = function () {
            return false;
        };
        console.log("[✓] Debugger kontrolleri bypass edildi.");
    } catch (e) {}

    // 5. java.lang.Runtime.getRuntime().exec() ("su", "which su", "getprop")
    try {
        var Runtime = Java.use("java.lang.Runtime");
        Runtime.exec.overload('java.lang.String').implementation = function (cmd) {
            if (cmd.indexOf("su") !== -1 || cmd.indexOf("which") !== -1) {
                console.log("[✓] Runtime.exec() engellendi: " + cmd);
                return this.exec("echo not_found");
            }
            return this.exec(cmd);
        };
    } catch (e) {}

    console.log("[✓] Anti-Root & Anti-Debug korumaları başarıyla devreden çıkarıldı!");
});
