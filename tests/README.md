# Arayüz ve regresyon testleri

## Çalıştırma

```powershell
python -m pip install flask playwright
python -m playwright install chromium
python -B -m unittest discover -s tests -v
```

`test_ui.py` gerçek Flask şablonunu ve yerel CSS/JavaScript dosyalarını Chromium ile açar. Cihaz API istekleri test verileriyle karşılanır; ADB, Frida, sertifika ve APK işlemleri cihazda çalıştırılmaz. Sunucu ve tarayıcı test bitince kapanır.

## Kapsam

- 320, 390, 768, 1024, 1440 ve 1920 piksel genişliklerinde sayfa taşması ve panel sınırları.
- Gelişmiş ayarların açılması; uygulama/script arama ve klavye ile seçim.
- Script değişikliği, Ctrl+S, başarı ve kaydetme hatası.
- 100 kayıt sınırından sonra canlı log yenilenmesi ve temizlenmesi.
- Mobil/masaüstü Logcat penceresi, filtreleme, Escape ve odağın geri dönmesi.
- Oturum başlatma/durdurma ve cihaz bağlantısının kesilmesi.
- Sunucu hatası ve bağlantı geri geldiğinde toparlanma.
- Proxy, sertifika, MEmu, Mitmweb, ADB ve hızlı işlem düğmelerinin API çağrıları.
- Beklenmeyen tarayıcı/JavaScript hataları ve yinelenen HTML kimlikleri.

Ekran görüntüleri `artifacts/ui/` dizinine yazılır. Görsellerdeki cihaz, uygulama ve log bilgileri test verileridir. Gerçek emülatörde Frida ve ağ trafiği için ayrıca uçtan uca doğrulama gerekir.


## Performans ve canlı doğrulama

Standart test komutu performans ve yavaş ağ testlerini de çalıştırır. `test_ui.py` ve `test_ui_latency.py` cihaz API'si bulunmayan ayrı bir Flask şablon sunucusu kullanır; gecikmeler tarayıcı yönlendirmeleriyle taklit edilir.

Gerçek cihazla, güncel kodu geçici bir yerel sunucuda ölçmek için:

```powershell
python -B tests/live_performance.py --isolated
```

Bu komut gerçek cihaz durumunu/listelerini ve zararsız cihaz yenileme işlemini kullanır. Uygulama verilerini silmez, APK veya sertifika yüklemez. Sonuçlar `artifacts/performance/after.json` dosyasına yazılır. Ayrıntılı sonuç ve kapsam `artifacts/performance/REPORT.md` içindedir.

Frida çalışır ve analiz oturumu kapalı durumdayken, gerçek tarayıcı üzerinden üç durdur–başlat döngüsü ve Frida protokol bağlantısını doğrulamak için:

```powershell
python -B tests/live_frida_start.py --run http://127.0.0.1:5002
```

Bu isteğe bağlı test cihazdaki Frida'yı yeniden başlatır ve çalışır bırakır. Standart test grubuna dahil değildir. Kanıtlar `artifacts/performance/frida-cold-start.json` ve `artifacts/ui/frida-cold-start.png` dosyalarındadır.

## Ayrı ADB işçisi ve gözetici

Panel, `tools/platform-tools/adb.exe` bulunduğunda Google ADB sürümünü kullanır. Cihaz yönetimi ayrı 5038 servisindedir. Frida ve yerel proxy veri tünelleri doğrudan MEmu'nun yerel adbd bağlantısını kullanır; Windows ADB servisinden veri taşımaz. Diğer tünel komutları için 5039 servisinde ayrı işçi/gözetici vardır. MEmu'nun ortak 5037 servisini değiştirmez. Yeni kurulumda `powershell -NoProfile -File tools/install_adb.ps1` ile resmi çalışma dosyaları indirilir. Standart Frida bağlantısı `27043 -> 27042` yolundadır; USB keşfi yapılmaz. Doğrudan tüneller kimlik doğrulama isteyen cihazları reddeder ve kesilen uygulama isteklerini tekrar göndermez.

Gerçek cihazda beş dakika boyunca salt okunur komutlar, açık Chromium paneli ve servis yeniden başlatma sayaçlarını test etmek için:

```powershell
python -B tests/live_adb_soak.py --run http://127.0.0.1:5000 --seconds 300
```

`python app.py` normal başlatma yoludur. Flask, bağımsız gözetici ve ADB işçisi ayrı süreçlerde çalışır. `test_adb_service.py` gerçek alt süreçler üzerinde süre aşımı, tamamen dondurulmuş işçi, dolmuş iletişim borusu, kuyruk süresi ve belirsiz işlemin tekrar edilmemesini sınar. Psutil yalnızca dondurma testleri için gereklidir; uygulamanın çalışma bağımlılığı değildir.

Canlı panelin kendi işçisini kontrollü olarak dondurmak için:

```powershell
python -B tests/live_adb_supervision.py --freeze-worker http://127.0.0.1:5000
```

Bu isteğe bağlı test panel boşta iken çalışır. Bağlı cihazla doğrulama için komuta `--connected-device` eklenir. Arka plandaki işçi değiştirilirken gerçek Chromium arayüzünün ve Flask durum sorgularının yanıt vermeye devam ettiğini doğrular. Kanıt: `artifacts/performance/live-supervision.json`. ADB servisinin kendisini veya kullanıcı cihazını bilerek kilitlemez.

Doğrudan proxy yolunu gerçek cihaz ve panel üzerinden beş yerel echo aktarımıyla doğrulamak için `python -B tests/live_direct_reverse.py --run http://127.0.0.1:5002` kullanılır. Test yalnızca kendi geçici proxy kuralını oluşturur ve kaldırır.
