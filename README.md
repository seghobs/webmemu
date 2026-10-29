# WebMEmu - SSL Pinning Bypass & Request Monitor

WebMEmu, Android emülatörleri (özellikle **MEmu Play**) ve gerçek cihazlar üzerinde **Frida** kullanarak SSL Pinning (SSL Sabitleme) engellerini kolayca aşmanızı ve giden HTTP istek başlıklarını (header) anlık izlemenizi sağlayan web tabanlı modern bir kontrol panelidir.

Özellikle Instagram ve Facebook gibi Meta uygulamaları üzerinde çalışmak, ağ trafiğini Burp Suite/Mitmproxy gibi araçlara yönlendirmek ve güvenlik önlemlerini atlatmak için tasarlanmıştır.

## 🚀 Öne Çıkan Özellikler

* **Otomatik Emülatör Bağlantısı**: MEmu emülatörüne (`127.0.0.1:21503`) tek tıkla otomatik ADB bağlantısı.
* **Otomatik Frida Server Kurulumu**: Cihazın CPU mimarisini algılayarak uygun `frida-server` sürümünü (v16.2.2) otomatik olarak indirir, cihaza yükler ve root yetkileriyle çalıştırır.
* **Gelişmiş SSL Pinning Bypass (`nev1.js`)**:
  * Conscrypt `TrustManagerImpl` atlatması.
  * `OkHttp3 CertificatePinner` devre dışı bırakma.
  * Özel `SSLContext` enjeksiyonu.
  * Facebook `CertificateVerifier` bypass'ı.
* **İstek Başlığı Yakalama ve Deşifre Etme (`headers.js`)**:
  * Instagram'ın giden ağ isteklerini dinleme.
  * Meta/Instagram tarafından kullanılan `x-meta-zca` ve diğer `x-ig-` / `x-meta-` başlıklarını izleme.
  * Base64 kodlu başlıkları (örneğin `x-meta-zca` içeriğini) otomatik olarak deşifre edip düz metin olarak gösterme.
* **Proxy Yönetimi**: Android global HTTP Proxy ayarlarını web paneli üzerinden saniyeler içinde açıp kapatabilme.
* **MEmu Reklam ve Bloatware Temizliği**: MEmu emülatöründeki gereksiz reklam ve yükleyici servislerini (`com.microvirt.market`, `com.microvirt.guide` vb.) devre dışı bırakarak performansı artırma.
* **Uygulama Yönetimi**: Cihazdaki üçüncü parti uygulamaları listeleme, çalışan işlemleri izleme ve tek tıkla uygulamaları sonlandırma (Force Stop).
* **Entegre Script Düzenleyici**: Panel üzerinden Frida Javascript scriptlerini düzenleyebilme, yeni scriptler kaydedebilme ve çalışan uygulamaya dinamik enjeksiyon yapabilme.
* **Canlı Log Ekranı**: Frida ve ADB süreçlerinden gelen tüm çıktıları renklendirilmiş (Başarılı: 🟢, Hata: 🔴, Bilgi: ⚪) olarak canlı izleme paneli.

---

## 🛠️ Kurulum ve Başlangıç

### Gereksinimler
* Bilgisayarınızda **Python 3.x** kurulu olmalıdır.
* Bilgisayarınızda **MEmu Play** emülatörü kurulu ve açık olmalıdır (Varsayılan ADB portu olan 21503 kullanılır).
* Cihazda/Emülatörde **Root yetkisi** etkinleştirilmiş olmalıdır.

### Çalıştırma
Projeyi başlatmak için ana dizindeki [**`run_web.bat`**](run_web.bat) dosyasına çift tıklamanız yeterlidir.

Bu betik:
1. Gerekli olan `Flask` kütüphanesini sisteminizde yüklü değilse otomatik olarak kuracaktır.
2. Web sunucusunu `http://127.0.0.1:5000` adresinde başlatacaktır.
3. Tarayıcınızı otomatik olarak açacaktır.

Alternatif olarak terminalden çalıştırmak için:
```bash
pip install flask
python app.py
```

---

## 📂 Dosya Yapısı

### Güncel arayüz ve ADB bağlantısı

Arayüz Flask, HTML ve JavaScript ile çalışır; koyu tema, mobil düzen, ayrı script editörü ve canlı konsol içerir. Cihaz işlemleri arka planda yürütülür; panel iş durumunu sorgular.

ADB komutları ayrı işçi ve gözetici süreçlerinde süre sınırıyla çalışır. Takılan işçi otomatik değiştirilir; belirsiz sonuçlanan komutlar tekrar gönderilmez. Eşzamanlı komut kuyruğu benzersiz sıra numarası kullanır.

Windows'ta resmi ADB çalışma kopyasını yüklemek için:

```powershell
powershell -NoProfile -File tools/install_adb.ps1
```

Cihaz yönetimi özel 5038 servis portunu kullanır. Yerel MEmu cihazında Frida ve proxy verisi doğrudan adbd TCP bağlantısından geçer; Windows ADB servisinden taşınmaz. Normal Frida ucu `127.0.0.1:27043` üzerinden cihazdaki `27042` portuna bağlanır. Bu doğrudan yol yalnızca yerel MEmu TCP cihazlarını destekler ve kimlik doğrulama isteyen cihazları reddeder. Diğer tünel türleri için ayrı 5039 servisi vardır.

### Testler

```powershell
python -m pip install flask playwright psutil
python -m playwright install chromium
python -B -m unittest discover -s tests
```

ADB kuyruğu, süre sınırları, otomatik kurtarma, tünel izolasyonu ve tarayıcı arayüzü sınanır. Gerçek cihaz testleri ayrı ve isteğe bağlıdır; komutları ve kapsamı [test belgesinde](tests/README.md) bulunur. Yerel kayıtlar, APK dosyaları ve test ekran görüntüleri Git'e eklenmez.

*   `app.py`: Arka planda ADB, Frida ve cihaz yönetim süreçlerini yöneten Flask API sunucusu.
*   `templates/index.html`: Kullanıcı dostu, modern, dinamik ve tek sayfalık web arayüzü.
*   `nev1.js`: SSL Pinning bypass işlemlerini gerçekleştiren ana Frida scripti.
*   `headers.js`: Instagram ağ isteklerini dinleyen ve deşifre eden Frida scripti.
*   `run_web.bat`: Kolay çalıştırma betiği.
*   `1.bat` & `2.bat`: Manuel işlemler için kullanılan test ve hızlı erişim betikleri.

---

## ⚠️ Dikkat Edilmesi Gerekenler

* Proje, `C:\Program Files\Microvirt\MEmu\adb.exe` yolundaki varsayılan MEmu ADB bileşenini kullanmaktadır. Eğer emülatörünüz farklı bir klasöre kuruluysa, `app.py` içerisindeki `self.adb_path` değerini kendi bilgisayarınıza göre güncelleyebilirsiniz.
* Frida Server'ın indirilebilmesi için bilgisayarınızın aktif internet bağlantısı olması gerekmektedir. İndirilen ikililer proje klasöründe saklanır, böylece sonraki çalıştırmalarda tekrar indirme yapılmaz.

### Yerel API erişimi

Panel yalnızca `127.0.0.1`, `localhost` veya `::1` Host adreslerini kabul eder. Origin başlığı varsa panel adresiyle aynı olmalıdır. Tüm `/api/` istekleri, her sunucu başlangıcında yenilenen `X-WebMEmu-Token` başlığını gerektirir. Tarayıcı bu anahtarı ana sayfadaki `webmemu-token` meta alanından otomatik alır. Harici test/API istemcileri önce `/` sayfasını okuyup aynı alanı başlıklarına eklemelidir; anahtarı URL'ye veya kalıcı kayıtlara yazmayın.

### Akıllı bypass

Hedef uygulamayı seçip **Akıllı bypass** düğmesine basın. Kurulu base/split APK dosyaları geçici klasöre okunur; toplam 256 MB ve DEX taramasında 128 MB sınırı vardır. Java adayı için yerel `universal_unpin.js` seçilir, Frida/Mitmweb/proxy/CA hazırlanır ve uygulama spawn modunda açılır. Uygulamada ağ isteği oluşturun. İnternetten script indirilmez, APK değiştirilmez veya yeniden imzalanmaz.

`_smart_observer.js` hedef süreçteki desteklenen OkHttp/HttpsURLConnection yanıtlarını izler. HTTPS yanıtının sertifikası yerel Mitmweb CA açık anahtarıyla doğrulanırsa **HTTPS proxy doğrulandı** gösterilir. Bu, uygulamanın daha önce pinning kullandığının kanıtı değildir. Hook yükleme mesajı veya başka uygulamanın proxy trafiği başarı sayılmaz. İzleyicinin kapsamadığı kütüphaneler, trafik oluşmaması veya 90 saniyelik bekleme **Doğrulanamadı** sonucudur; bu, bypassın kesin başarısız olduğu anlamına da gelmez. Flutter, Unity, Xamarin ve Cronet işaretleri otomatik native yama yerine **Desteklenmiyor** verir. Bunlar sürüme özel analiz gerektirir. Mevcut elle script seçimi korunur. Oturumun Durdur düğmesi izleyiciyi de durdurur.

Araştırma kaynakları: [Android Network Security Configuration](https://developer.android.com/privacy-and-security/security-config), [Frida Android](https://frida.re/docs/android/), [Mitmproxy Certificates](https://docs.mitmproxy.org/stable/concepts/certificates/), [Objection pinning komutu](https://github.com/sensepost/objection/blob/master/objection/console/helpfiles/android.sslpinning.disable.txt), [HTTP Toolkit Android rehberi](https://httptoolkit.com/docs/guides/android/). Diğer yöntemler (debug CA, APK yeniden paketleme, sürüme özel native müdahale) araştırıldı; bu sürümde otomatik yürütülmez.
