# ACCUNIQ Manager QR web servisi — canlı doğrulama

4 Ekim 2026 · Adres: https://qr.accuniq.com/accuniq?bcadata=

## Önceki açıklamanın düzeltilmesi

`bca-app2.accuniq.com` üzerindeki mobil uygulama API'si ve `qr.accuniq.com` üzerindeki ACCUNIQ Manager ayrı servislerdir. Birincisi önceki testte 502 döndürdü; QR sonuç görüntüleyicisi bu testte HTTP 200 döndürdü. Dolayısıyla "ACCUNIQ bulutu çalışmıyor" şeklinde genel bir sonuç çıkarılamaz.

QR web servisi cihazın ürettiği ölçüm verisini işleyebilir. Bu işlev ile cihazın sensörüne/seri portuna doğrudan bağlanmak farklıdır. Kullanıcının ekran görüntüsü QR sonuç görüntüleyicisini gösterir; tek başına Bluetooth/USB veya cihazdan sunucuya otomatik bağlantıyı kanıtlamaz.

## Kontrollü gerçek istekler

Canlı servise APK kod yorumundaki sabit örnek QR ile istek gönderildi. Bu örnek canlı kullanıcı hesabından alınmadı. İkinci testte örnek yükün 46:50 ağırlık alanı `0700` değerinden `0723` değerine değiştirildi; diğer vücut bileşimi alanları aynı tutuldu. Bu bir cihaz ölçümü değildir; alan eşlemesini sınayan kontrollü girdidir.

| Test | HTTP | Web sayfasındaki kilo | Kas | Vücut suyu |
|---|---:|---:|---:|---:|
| APK sabit QR örneği | 200 | 70.0 kg | 53.5 kg | 41.7 L |
| Ağırlık alanı değiştirilmiş örnek | 200 | 72.3 kg | 53.5 kg | 41.7 L |
| Boş `bcadata` | 200 | 66.1 kg | 37.5 kg | 29.7 L |

Örneğin yerel FastAPI ayrıştırıcısı da aynı iki girdiyi 70.0 ve 72.3 kg olarak çözdü. Ağırlığın URL girdisiyle birlikte değişmesi, bu istekteki verinin QR yükünden işlendiğine güçlü kanıttır. Sunucunun kendi iç kaynak kodu veya veritabanı incelenmedi.

Boş `bcadata` ile de ölçüm benzeri değerler gösterildi. Bu değerlerin varsayılan/test verisi veya başka sunucu davranışı olmasının nedeni doğrulanmadı. Boş parametreyle bir sonuç ekranının açılması, bağlı cihazdan o anda ölçüm alındığını kanıtlamaz. Kullanıcı ekran görüntüsündeki ölçüm bu testte sorgulanmadı; adres çubuğundaki tam QR yükü görüntüde bulunmuyor.

## Tarayıcı ağ testi

Gerçek Chromium ile aynı örnek açıldı. Toplam 7 ağ isteği gözlendi; bütün istekler `qr.accuniq.com` alan adına gitti. Sayfa yüklenirken XHR, fetch veya WebSocket isteği görülmedi. Ölçüm değerleri ilk HTML yanıtında zaten bulunuyordu. Bu nedenle gözlenen akışta ayrı JSON ölçüm API'si kullanılmadan sunucu HTML sonucu üretiyor.

HTML içindeki `showPopup` işlevi `accuniq/showLayer?type=...` yolunu açıklama pencereleri için kullanır. Bu yol ölçüm alma veya donanım bağlantısı olarak doğrulanmış değildir. Tarayıcı yükleme testinde açıklama düğmelerine basılmadı.

## Doğrulanan veri yolu

```text
BC380 ölçüm sonucu
  → cihazın ürettiği QR içindeki bcadata
  → telefon/bilgisayar QR'ı okur ve URL'yi açar
  → qr.accuniq.com QR yükünü işleyip HTML raporu döndürür
```

Kendi dashboard'umuz aynı yükü yerel FastAPI'de ayrıştırır ve SQLite'a kaydeder. QR görüntüleyicisine bağlanması gerekmez. Canlı karşılaştırma, kilo/kas/su alanları için yerel ayrıştırıcının çıktısıyla üretici web ekranının uyuştuğunu doğruladı. Bu test tüm 145 alanın tüm sürümler için doğrulanması değildir.

## Sınırlar ve bağlantı koşulu

- Bu testte fiziksel BC380 bağlı değildi. USB/Bluetooth veya cihazın Wi-Fi/Ethernet üzerinden otomatik upload davranışı sınanmadı.
- `qr.accuniq.com` için varsayılan Python TLS doğrulaması `CERTIFICATE_VERIFY_FAILED` döndürdü. Kamuya açık, kimlik bilgisi içermeyen örnek sayfa yalnızca bu araştırmanın izole isteklerinde ve Chromium test bağlamında sertifika doğrulaması gevşetilerek okundu. Dashboard'un TLS ayarları değiştirilmedi; oturum bilgisi veya gerçek kullanıcı QR'ı gönderilmedi.
- Doğrudan cihaz bağlantısını doğrulamak için cihazın PC/iletişim ayarı, takılı dongle ve kullanılan üretici PC yazılımı/SDK'sı ya da gerçek cihaz trafiği gerekir. QR web sayfası USB/Bluetooth paket biçimini ortaya çıkarmaz.

Kanıt dosyaları: `artifacts/accuniq/qr-web/controlled-tests.json`, `browser-network.json`, ham HTML yanıtları ve `sample-rendered.png`.
