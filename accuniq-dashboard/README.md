# ACCUNIQ Studio

ACCUNIQ BC380 için yerelde çalışan ölçüm dashboard'u. **Frontend Next.js 16, backend Python FastAPI, veritabanı SQLite.**

## Başlatma

Node.js 20.9+ ve Python 3.10+ gerekir. Windows'ta **`çalıştır.bat`** dosyasını çift tıklayın (`run_dashboard.bat` da çalışmaya devam eder). Gerekli paketleri kurar, frontend'i derler ve iki servisi yalnızca loopback üzerinde başlatır.

### Mac

Proje klasörünü Mac'e kopyalayın. `mac-launcher.zip` dosyasını **aynı klasöre** çıkarıp içindeki **`çalıştır.command`** dosyasını çift tıklayın. ZIP içindeki dosya çalıştırma izniyle hazırlanmıştır. Kaynak `.command` dosyasını doğrudan kopyaladıysanız Terminal'de proje klasöründen bir kez `chmod +x ./çalıştır.command` çalıştırın; ardından çift tıklayabilirsiniz.

Mac başlatıcısı ayrı `.venv-macos` Python ortamı oluşturur, Mac'e uygun Node paketlerini kurar ve her iki servisi arka planda başlatır. İlk açılış kurulum ve derleme nedeniyle daha uzun sürebilir. Sonraki açılışlarda mevcut kurulumu kullanır. Dashboard hazır olduğunda tarayıcı açılır; zaten çalışıyorsa tekrar servis başlatılmaz. Kayıtlar `backend/data` klasöründedir. Node.js ve Python'un Terminal üzerinden erişilebilir olması gerekir.

- Dashboard: http://127.0.0.1:3001/
- FastAPI: http://127.0.0.1:8010/
- Yerel API belgesi: http://127.0.0.1:8010/docs

Manuel kurulum:

```powershell
cd accuniq-dashboard
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

İkinci terminal:

```powershell
cd accuniq-dashboard/frontend
npm ci
npm run build
npm start
```

Frontend `/api` çağrılarını FastAPI'ye yönlendirir. Next.js içinde cihaz veya ölçüm backend'i yoktur. Backend portunu değiştirirseniz `BACKEND_URL` ortam değişkenini ayarlayıp frontend'i tekrar derleyin. Veritabanı yolu `ACCUNIQ_DB_PATH` ile değiştirilebilir; varsayılan dosya `backend/data/studio.sqlite3`.

## Çalışan özellikler

- Yerel profil oluşturma ve profil bazlı ölçüm geçmişi.
- QR metni/bağlantısı, görselden QR okuma ve izinli kamera taraması.
- Kaydetmeden önce QR önizleme; biçim, tarih, temel metrik ve birim kontrolü.
- Aynı QR'ı tekrar kaydetmeme; başka profile sessizce taşımama.
- Ağırlık/kas/yağ trendleri, son ölçüm özeti, detay ve ham alanlar.
- CSV dışa aktarma ve JSON yedek indirme.
- Gerçek profillerden ayrı, açıkça etiketlenmiş sentetik örnek profil.
- APK'dan çıkarılan 403 servis/action tanımı ve 145 alanlık QR referansı.
- Masaüstü, tablet ve mobil düzen; klavye ile diyalog kontrolü.

## Cihaz bağlantısının kapsamı

Bugün çalışan veri yolu **QR içe aktarma**dır. Cihazın bağlı olması gerekmez. İncelenen Android ACCUNIQ 1.2.29 uygulaması QR metnini ayrıştırır; genel BLE yardımcı kodu aktif cihaz bağlantısını kanıtlamaz.

USB ve Bluetooth için panelde durum alanları ve backend'de ayrı `device.py` sınırı bulunur. Portlar yalnızca okunur. **Baud rate, GATT UUID veya cihaz komutu tahmin edilmez; gerçek cihaz protokolü doğrulanana kadar fiziksel bağlantı kurulmaz.** Port görünmesi cihazın BC380 olduğunu kanıtlamaz.

Referans bölümündeki ACCUNIQ HTTP API yolları yerel bir sözlüktür. Buluta istek gönderilmez, token saklanmaz ve bulut hesabı kullanılmaz. Kamera/görsel QR okuma tarayıcıda yapılır; görsel sunucuya yüklenmez.

## QR desteği

### Native DEX analizi ve aktarım köprüsü

`Cihaz & aktarım` ekranındaki native analiz bölümünden rapor ve tam DEX envanteri indirilebilir. 2.856 sınıfın ve 19.257 tanımlı metodun komutları tarandı; QR/SQLite/Cordova köprüsüne ait 14 sınıf Java benzeri kaynak olarak yeniden oluşturuldu. [Ayrıntılı rapor](backend/reference/DEX-ANALIZ-RAPORU.md), bulguların sınırlarını da içerir.

Kamera ve QR görseli aktarımı, APK'daki `BarcodeScanner.onActivityResult` sözleşmesini kullanır:

```json
{"text":"<ACCUNIQ QR metni veya URL>","format":"QR_CODE","cancelled":0}
```

- `POST /api/qr/scanner/preview`: bu gövdeyle önizleme.
- `POST /api/qr/scanner/import`: aynı gövdeye `patient_id` ve isteğe bağlı `note` ekleyerek kayıt.
- `GET /api/analysis/dex`: kaynak kanıtları ve sayısal envanter.
- `GET /api/analysis/dex/report`: ayrıntılı rapor.
- `GET /api/analysis/dex/archive`: sınıf/metot/string/çağrı envanteri, bytecode ve ilgili Java çıktıları.

İptal edilen, boş veya QR dışı tarama kayıt oluşturmaz. Bu köprü bir USB/Bluetooth bağlantısı değildir; doğrulanan QR veri yoludur. Görsel testi APK'daki sabit örnekten oluşturulan `apk-sample-qr.png` ile çalışır; gerçek kullanıcı veya cihaz kaydı değildir.

Desteklenen biçim `http(s)://qr.accuniq.com/accuniq?bcadata=...` veya ham **527 karakterlik, `Mg` başlıklı ve `@` ile biten** yüktür. Uzunluğu farklı QR sürümleri açık hata verir. URL içindeki işaretli `+` değerleri korunur.

Metrik QR'da kg/cm değerleri çözülür. Birim kodu 1/3 olan imperial QR'ın boy alanı kaynak uygulamada iki farklı kodlama taşıyabildiği için boy dönüştürülmez; diğer değerler kaynak birimiyle tutulur. Trendlerde yalnızca aynı birimdeki kayıtlar karşılaştırılır. QR alan biçimi doğrulaması bir kriptografik bütünlük/doğruluk garantisi değildir.

Her kayıt orijinal 145 alanı, uyarıları, kaynak etiketini ve tarihini korur. QR'daki tarih saat dilimsizdir; kayıt zamanı UTC olarak ayrıca tutulur. Grafikler klinik değerlendirme veya tıbbi öneri üretmez.

`backend/reference` içindeki CSV dosyaları kullanıcının APK analizinden gelen yerel tanımlardır. `qr-schema.json`, `app.all.js:6244–6389` sabit alan haritasını temsil eder. Diğer ekran/sürüm varyantları kaynak CSV'de korunur; otomatik olarak aynı format kabul edilmez.

## Testler

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m playwright install chromium
python -B -m unittest discover -s backend/tests -v
cd frontend
npm run build
cd ..
python -B tests/test_browser.py
```

Tarayıcı testinden önce 8010 ve 3002 portları boş olmalıdır. Test kendi FastAPI ve üretim Next.js süreçlerini başlatır, ayrı `test-output/ui-test.sqlite3` kullanır ve kendi süreçlerini kapatır. Cihaza/buluta bağlanmaz. Kayıt/önizleme/tekrar kayıt, dışa aktarma, API arama, mobil düzen, kamera reddi ve bağlantı hatasından dönüş test edilir.

`backend/tests/fixtures/apk-sample.txt`, APK kod yorumundaki sabit test örneğidir; kullanıcı hesabından veya gerçek cihaz oturumundan alınmadı. Yerel veritabanları, bağımlılıklar ve test çıktıları Git dışında tutulur.
