# ACCUNIQ 1.2.29 — DEX ve cihazdan veri alma analizi

Tarih: 4 Ekim 2026 · Paket: `kr.co.bca` · Hedef cihaz: BC380

## 1. Sonuç ve önceki incelemenin kapsamı

Önceki inceleme DEX metod referanslarını kontrol etmişti; bütün metot gövdelerinin dökümünü ve native köprünün Java karşılığını sunmamıştı. Bu rapor bu eksikliği tamamlar.

**İncelenen APK'da cihazın ölçümü uygulamaya QR kodunun okunmasıyla gelir. DEX içinde BC380'e Bluetooth, USB veya seri port üzerinden bağlanan bir uygulama metodu bulunamadı.** BluetoothManager ve UsbManager referansları AndroidX'in genel sistem servisi eşlemesidir; bağlantı işlevi değildir. Bu bulgu BC380 donanımının başka yazılımla bağlanamayacağı anlamına gelmez.

Projedeki `classes.dex`, APK'nin içindeki `classes.dex` ile byte düzeyinde aynıdır. Yeni veya farklı uygulama sürümü değildir. SHA-256:

`6c3f3677816cf8812121c6381956d71a18fd9bdbe31be9ade20736397780a4f3`

## 2. Eksiksiz envanterin kapsamı

| Öğe | Adet |
|---|---:|
| DEX dosyası | 1 |
| Dosya büyüklüğü | 3.442.328 byte |
| Tanımlı sınıf | 2.856 |
| Tanımlı metot | 19.257 |
| DEX metot referansı | 26.339 |
| Metot çağrısı komutu (invoke) | 64.837 |
| String tablosu girdisi | 29.160 |
| Native metot bildirimi | 24 |
| Java benzeri kaynak olarak yeniden oluşturulan ilgili sınıf | 14 |

Tanımlı metot sayısı ve metot referansı sayısı farklıdır: referans tablosu Android/Java gibi APK dışında uygulanan metotları da içerir. 19.257 metodun tamamı için erişim bayrakları, imza ve gövde varlığı kaydedildi; gövdesi bulunan bütün metotların tüm komutları tarandı. 14 ilgili sınıf ayrıca Androguard DAD ile decompile edildi; bu aşamada hata olmadı. Diğer sınıfların kaynak karşılığı yerine eksiksiz bytecode dökümü sunulur. Decompiler çıktısı orijinal Java kaynak kodu değildir; değişken tipleri ve adları hatalı yeniden kurulabilir. İddiaların asıl kanıtı DEX komutlarıdır.

`dex-inventory.zip` içeriği:

- `classes.csv`: bütün sınıflar, üst sınıflar, arayüzler ve metot adetleri.
- `methods.csv`: bütün tanımlı metotların imzaları ve erişim bayrakları.
- `calls.csv`: tüm invoke komutları, çağıran metot ve byte offset.
- `strings.csv`: bütün string tablosu girdileri; URL/servis adları da dahildir.
- `packages.csv`: paket bazında sınıf sayıları.
- `all-bytecode.txt`: tüm sınıf, alan ve metot komut dökümü.
- `evidence.csv`: Bluetooth/USB/seri port, kamera/QR, SQLite, ağ ve reflection eşleşmeleri.
- `summary.json`: dosya kimliği ve tarama sonuçları.
- `java/`: bağlantı/veri akışıyla ilgili yeniden oluşturulmuş 14 sınıf.
- `analyze_dex.py`: analizi tekrar üretmek için kullanılan betik; özgün klasör yapısı ve Androguard 4.1.3 gerekir.

## 3. Uygulamanın başlangıcı

`kr.co.bca.MainActivity` sadece iki metot tanımlar: constructor ve `onCreate(Bundle)`. `onCreate` Cordova başlangıcını çağırır, `cdvStartInBackground` seçeneğini kontrol eder ve `loadUrl(this.launchUrl)` ile web uygulamasını yükler. Burada cihaz eşleştirme, ağ ölçüm servisi veya Bluetooth başlatma kodu yoktur. `kr.co.bca` altındaki diğer sınıflar ağırlıklı olarak Android kaynak kimlikleri (`R`, `R$...`) ve `BuildConfig` içerir.

DEX'in büyük bölümü AndroidX/Android destek sınıfları, Cordova, ZXing, resim seçme/kırpma, dosya, kamera ve SQLite eklentileridir. Sınıfın APK'da bulunması o özelliğin uygulama tarafından kullanıldığını tek başına kanıtlamaz.

## 4. Native QR taramasının adımları

1. JavaScript `$cordovaBarcodeScanner.scan({formats: "QR_CODE", ...})` çağırır (`app.all.js:5804`).
2. Cordova plugin kaydı bu isteği `com.phonegap.plugins.barcodescanner.BarcodeScanner` sınıfına yönlendirir (`res/xml/config.xml`, BarcodeScanner feature).
3. `BarcodeScanner.execute` action `scan` ise CAMERA iznini kontrol eder. İzin varsa `scan`, yoksa `requestPermissions` çağrılır.
4. `scan` Cordova thread pool'una `BarcodeScanner$1` görevini verir.
5. Görev ZXing `CaptureActivity` için `com.google.zxing.client.android.SCAN` intent'i oluşturur. Kamera, torch, formats ve prompt seçeneklerini geçirir; request code **47740** ile `startActivityForResult` çağrılır.
6. Kamera QR'ı çözünce Android intent içinde `SCAN_RESULT` ve `SCAN_RESULT_FORMAT` alanları döner.
7. `BarcodeScanner.onActivityResult` bunları `{text, format, cancelled}` JSON'una çevirip `CallbackContext.success` ile Cordova'ya iletir.
8. Cordova sonuç kuyruğu JavaScript callback'ini çalıştırır. Uygulama `cancelled` durumunu kontrol eder, QR'ı doğrular, `bcadata` yükünü ayırır, önizleme ekranını açar.

Başarılı sonuç sözleşmesi:

```json
{"text":"https://qr.accuniq.com/accuniq?bcadata=<ölçüm yükü>","format":"QR_CODE","cancelled":0}
```

İptalde text/format boş ve cancelled 1 olarak döner. Kamera izni reddinde plugin hata sonucu verir. **Bu JSON cihazın USB/Bluetooth paketi değildir; kameranın okuduğu metni teslim eden uygulama içi sözleşmedir.**

## 5. Ölçüm metni nasıl veriye dönüşür?

Native QR tarayıcı kilo, yağ veya kas hesaplamaz. Sadece çözümlenen metni teslim eder. JavaScript `getBarcodeData`, HTTP önekinde 38, HTTPS önekinde 39 karakteri çıkarır (`app.all.js:5990`). `getAwardData` yükü sabit karakter aralıklarına böler (`6244–6389`).

| Alan | Başlangıç:bitiş | İşlem |
|---|---|---|
| Komut | 0:2 | `Mg` |
| Sürüm | 2:8 | Kaynaktaki sürüm işareti |
| Ölçüm tarihi | 8:18 | YYMMDDHHmm |
| Ölçüm/cihaz kimlik alanı | 18:38 | Ham metin; profil kimliği diye otomatik kullanılmaz |
| Cinsiyet / yaş | 38:39 / 39:42 | Kod / sayı |
| Boy / ağırlık | 42:46 / 46:50 | Metrik biçimde ölçekli değer |
| Kas | 86:90 | 10'a bölünür |
| Su | 110:114 | 10'a bölünür |
| Yağ kütlesi | 154:158 | 10'a bölünür |
| BMI | 198:202 | Kaynak değeri 10'a bölünür |
| Yağ yüzdesi | 222:226 | 10'a bölünür |
| BMR / kalori | 337:341 / 341:345 | Kaynaktaki sayılar |
| Birim bayrağı | 525:526 | 0 metrik; 1/3 imperial dalları |
| Bitiş | 526:527 | `@` |

Mevcut web uygulaması 527 karakterlik sabit varyantın 145 alanını korur. APK'da başka ekran/sürüm dalları ve 520 üzeri uzunluk kontrolleri de vardır; bütün varyantları aynı format saymak doğru değildir. Imperial boy alanındaki belirsizlik devam eder; tahmini dönüşüm yapılmaz. QR'daki kimlik alanı kendi başına kullanıcı yetkisi veya kriptografik imza değildir.

## 6. SQLite ve JNI yolu

JavaScript `band.db` açar ve `bodyTable`, `targetTable`, `logTable` tablolarını kurar (`app.all.js:152,177,196,203`). `SQLitePlugin.execute` open/close/delete/executeSqlBatch gibi eylemleri işler. DBRunner ve DBQuery sınıfları istekleri kuyruk üzerinden yürütür. Android SQLite ve SQLiteConnectorDatabase yolları yerel SQL işlemlerini sağlar.

24 native metot bildiriminin tamamı `io.liteglue.SQLiteNDKNativeDriver` sınıfındadır: DB açma/kapatma, statement hazırlama, bind, step ve column okuma işlemleri. ARM64 split APK'da tek `.so` dosyası `lib/arm64-v8a/libsqlc-ndk-native-driver.so` olarak bulunur. DEX'in JNI bildirimleri bu SQLite sürücüsüyle ilişkilidir; cihaz bağlantı sürücüsü bildirimi bulunmadı. `.so` makine kodunun tamamı bu raporda disassemble edilmedi; DEX ve native kütüphane envanteri kanıtı bundan ayrıdır.

## 7. Bluetooth, USB, seri port ve reflection denetimi

Tüm komutlarda Android Bluetooth, USB, seri port sınıfları; GATT/RFCOMM bağlantıları, bulk/control transfer, seri port yolu ve baud ifadeleri tarandı.

- Bluetooth: tek eşleşme `ContextCompat$LegacyServiceMapHolder.<clinit>` offset `0x11e`: `const-class BluetoothManager`.
- USB: tek eşleşme aynı metotta offset `0x2cc`: `const-class UsbManager`.
- Seri port: seçilen terimlerde eşleşme yok.
- Bluetooth stringleri: `BluetoothManager`, `STREAM_BLUETOOTH_SCO`, `bluetooth`. Ses sabiti ve sistem servis adı BC380 bağlantısını kanıtlamaz.
- `BluetoothGatt`, `BluetoothSocket`, `connectGatt`, `createRfcomm`, `bulkTransfer` gibi gerçek aktarım çağrıları bulunmadı.
- Manifestte Bluetooth izinleri yok; Cordova plugin kayıtlarında BLE/serial plugin yok.
- Reflection/class loading/JNI yükleme eşleşmeleri de envantere alındı (947 komut eşleşmesi). Genel Cordova/Android kütüphane altyapısı vardır; eşleşme sayısı 947 ayrı cihaz işlevi anlamına gelmez.

DEX taraması statik bir incelemedir. Sunucudan indirilen olası kod, farklı APK/split sürümleri, donanım firmware'i veya çalışma anında yapılan işlemler bu sonuçla bütünüyle dışlanamaz. Bu sürümde bir UUID, PIN, baud rate veya cihaz çerçevesi üretmek için kanıt bulunmamıştır.

## 8. Ağ, eklentiler ve diğer kodlar

Ağ kategorisindeki 149 komut eşleşmesi Java network/HTTP yardımcılarını içerir. Cordova dosya aktarımı, WebView, URL ve TLS kodları ölçüm cihazından Bluetooth veri alındığı anlamına gelmez. Uygulamanın HTTP endpoint ve ölçüm senkronizasyon mantığı JavaScript web varlıklarındadır; önceki 403 API tanımı ve ekran eşlemesi geçerlidir. DEX stringlerini HTTP endpoint sanıp canlı istek atılmadı.

Kamera/QR kategorisinde 12.279, SQLite kategorisinde 566 komut eşleşmesi vardır. Bunlar komut adedidir; bağımsız özellik veya aktif ekran sayısı değildir. Diğer eklentiler: dosya, resim seçme/kırpma, kamera, globalizasyon, cihaz bilgisi, ağ durumu, splashscreen, toast, klavye, uygulama içi tarayıcı ve e-posta. Tam sınıf/metot listesi ZIP'tedir. Bunların BC380 protokolüyle ilgisi ayrıca kanıtlanmış değildir.

## 9. Dashboard'a yapılan entegrasyon

- Kamera ve görsel QR okuyucusu native tarayıcıyla aynı `{text,format,cancelled}` sözleşmesini kullanır.
- FastAPI `/api/qr/scanner/preview` bu sözleşmeyi doğrular; iptal, boş sonuç ve QR dışı biçimi reddeder. Kaydetmeden önizleme yapılır.
- `/api/qr/scanner/import` aynı sözleşme + `patient_id` + isteğe bağlı `note` alır. Mevcut doğrulama, tekrar kayıt ve profil korumasını kullanır.
- Düz metin QR önizleme/aktarım endpoint'leri çalışmaya devam eder.
- `/api/analysis/dex` makine tarafından okunabilen kanıt ve envanter özeti sunar.
- `/api/analysis/dex/report` bu raporu; `/api/analysis/dex/archive` bütün DEX envanterini indirir.
- Cihaz & aktarım ekranı native veri akışını, sayıları ve rapor indirme bağlantılarını gösterir.

**Entegre edilen yöntem doğrulanmış QR veri teslimidir. USB/Bluetooth otomatik canlı ölçüm aktarımı henüz uygulanmış veya test edilmiş değildir.** Bunun için başka üretici PC yazılımı/SDK'sı ya da fiziksel BC380 ile gerçek bağlantı trafiği gerekir. Bu çalışma cihaz bağlı olmadan yapıldı; bir canlı cihaz ölçümü alınmadı.
