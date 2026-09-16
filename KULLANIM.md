# Çevirgeç — GUI ve CLI

Bu program, DRM içermeyen metin tabanlı EPUB dosyalarını LM Studio'da çalışan yerel bir modelle Türkçeye çevirir. Windows arayüzü PySide6 kullanır. Eski komut satırı seçenekleri korunmuştur.

## Kurulum

Python 3.10 veya daha yeni bir sürüm kurulu olmalıdır. Arayüzü kaynak koddan çalıştırmak için:

```powershell
py -3 -m pip install -r requirements.txt
GUI_BASLAT.cmd
```

LM Studio'da model yüklenmeli ve Developer bölümündeki yerel sunucu başlatılmalıdır. Varsayılan adres `http://127.0.0.1:1234/v1` değeridir.

Ana ekranda hem ayarlarda seçilen model hem de LM Studio'da fiilen yüklü model görünür. Yüklü model satırında aktif context uzunluğu da gösterilir. **Yenile** düğmesi LM Studio durumunu yeniden sorgular.

Model alanı serbest metin değildir. LM Studio birden fazla yüklü model bildirirse **Ayarlar** içindeki listeden seçim yapılır. Yalnızca bir model yüklüyse Çevirgeç onu otomatik seçer. Ayarların üstündeki uyarı teknik değerlerin çeviri kararlılığını etkilediğini belirtir; **Varsayılan ayarlara dön** düğmesi model dışındaki teknik değerleri başlangıç ayarlarına çevirir ve mümkünse varsayılan yüklü modeli seçer.

## Arayüz kullanımı

1. Ana çalışma klasörünü seçin.
2. Kaynak EPUB dosyasını seçin.
3. **Çeviriyi Başlat** düğmesine basın.
4. Kitaba özgü terimler varsa İngilizce ve Türkçe karşılıklarını girin. Sözlük boş bırakılabilir.
5. İsterseniz **Ön analiz yap (isteğe bağlı)** seçeneğini işaretleyin. Varsayılan olarak kapalıdır. Açılır pencereden yalnızca ön analize dahil edilecek bölümleri seçin; kapak, telif ve yazar tanıtımı gibi alanlar otomatik öneride işaretlenmez.
6. Çalışma sırasında aynı düğme **Duraklat**, **Duraklatılıyor…** ve **Devam Et** durumlarına geçer.

Ön analiz kitabı çeviriden önce parçalar halinde inceleyerek karakter, terim, üslup, değişim ve belirsizlik notları üretir. Seçeneğin araç ipucunda da belirtildiği gibi toplam işlem süresini iki kata kadar uzatabilir. Sonuç **Ön Analiz** sekmesinde ve `BOOK-ANALYSIS.md` dosyasında görülür. Analiz önerileri `glossary.json` dosyasını değiştirmez; kullanıcının zorunlu sözlüğü her zaman önceliklidir.

Analiz bilgileri iki kapsama ayrılır. Model yalnızca zamandan ve olay örgüsünden bağımsız bilgileri kitap geneli adayı olarak işaretler. Motor bu adayları en az iki ayrı bölümde aynı biçimde görülmeleri ve terim karşılıklarının çelişmemesi halinde kitap geneline yükseltir. Karakterin o bölümdeki ruh hâli, konuşma sesi, ilişki/rol değişimi ve yerel üslup yalnızca ilgili bölümün çeviri isteğine eklenir. Tam olay özetleri hiçbir çeviri prompt'una verilmez. Böylece sonraki bölümde öğrenilen geçici bir karakter özelliğinin önceki veya başka bir bölüme taşınma riski azaltılır; yine de ön analiz bir LLM çıktısı olduğu için insan doğrulamasının yerini tutmaz.

Bir bölümün çevirisi başladıktan sonra kullanılan referans o bölümün checkpoint'ine sabitlenir. Yeni ön analiz veya değişen analiz sonuçları devam eden bölümün ortasında uygulanmaz.

Ön analiz raporu ilk tamamlanan analiz parçasından itibaren **Ön Analiz** sekmesinde güncellenir. Sol bölüm listesindeki durumlar da aktif checkpoint'e göre `Ön analiz`, `Çevriliyor`, `Notlar hazırlanıyor`, `Devam edecek` veya `Tamamlandı` olarak yenilenir.

Ön analizde eksik veya bozuk model yanıtı alınırsa aynı parça en fazla iki kez otomatik yeniden denenir. Üç denemenin tamamı başarısız olursa işlem durur; tamamlanan parçalar korunur ve proje yeniden başlatıldığında ilk eksik parçadan devam edilir. Context hesabı, ayar veya checkpoint uyuşmazlığı gibi yapılandırma hataları otomatik tekrarlanmaz. Ön analizde yapay bir bitiş işareti aranmaz: tam ve geçerli JSON şeması tamamlanma ölçütüdür. Eksik alanlı, bozuk veya token sınırına ulaşmış yanıt checkpoint'e yazılmaz. Normal çeviri metinlerinde bitiş işareti denetimi güvenlik amacıyla kullanılmaya devam eder.

Çeviri sırasında tamamlanma işareti, boş/geçersiz yanıt, tool/düşünce işareti, zorunlu glossary veya geçici LM Studio bağlantı/HTTP hatası oluşursa aynı kaynak parça en fazla iki kez otomatik yeniden denenir. Hatalı yanıt hiçbir zaman parça checkpoint'ine eklenmez. Çıktı token sınırı, context sınırı, ayar ve checkpoint uyuşmazlıkları otomatik tekrarlanmaz; bunlar kullanıcı müdahalesi gerektirir.

Duraklatma, devam eden LM Studio isteğini yarıda kesmez. Yanıt doğrulanıp checkpoint'e yazıldıktan sonra uygulanır. Pencere kapatılırsa program yine mevcut isteğin bitmesini ve checkpoint'in kaydedilmesini bekler. İstek süresiz beklemesin diye Ayarlar menüsündeki timeout kullanılır.

Var olan bir proje sağ üstteki **Projeyi sürdür** düğmesiyle sürdürülebilir. Bu düğme daha önce oluşturulan `{KitapAdı}-ceviri` klasörünü doğrudan açar; kaynak EPUB'u yeniden içe aktarmadan tamamlanan parçaları ve checkpointleri kullanır. **Ayarlar** düğmesi de sağ üsttedir. Bölüm ağacından bir kayda tıklamak yalnızca kaynak ve çeviri önizlemesini açar; tekrar çeviri başlatmaz.

## Dosyalar

Seçilen ana klasörde `KitapAdi-ceviri` çalışma klasörü oluşturulur:

- `source/`: EPUB'dan çıkarılan kaynak Markdown bölümleri
- `translation/`: tamamlanan Türkçe bölümler
- `_python_translation/`: atomik checkpoint ve devam kayıtları
- `_python_analysis/`: isteğe bağlı ön analiz checkpoint ve JSON kayıtları
- `BOOK-ANALYSIS.md`: okunabilir ön analiz raporu (ön analiz seçildiyse)
- `00-CONTEXT.md`: kalıcı çeviri kararları ve bölüm durumları
- `glossary.json`: kullanıcının kitap özelindeki zorunlu terimleri
- `translation.log`: konsol kayıtları, hatalar ve token bilgileri
- `TAM-CEVIRI.md`: birleştirilmiş çeviri
- `Orijinal Ad TR.epub`: çevrilmiş EPUB

Aynı çıktı mevcutsa `TR-2`, `TR-3` biçiminde yeni ad kullanılır. Kaynak EPUB değiştirilmez.

## EPUB çıktısı

Çıktı EPUB:

- Kaynak metadata öğelerini korur; dil alanını `tr` yapar.
- ISBN varsa kaynak kimliği olarak korur; ISBN yoksa yeni ISBN üretmez.
- Kaynak kapak, görsel, font ve diğer ikili varlıkları kopyalar.
- Kaynakta zaten spine/TOC içinde bulunan kapak bölümünü kullanır; ikinci bir sentetik kapak sayfası eklemez.
- TOC etiketini görünür sayfa başlığına dönüştürmez. Kaynakta görünür başlık yoksa kapak, başlık sayfası ve bölüm gövdesine yapay `<h1>` eklemez.
- Yeni EPUB3 `nav.xhtml` ve EPUB2 uyumluluğu için `toc.ncx` oluşturur.
- İçindekiler başlığında çeviri Markdown dosyasındaki ilk `#` başlığını kullanır.
- Başlık bulunamazsa bölüm dosya adına döner.
- İlk `dc:title` metadata değeri `Orijinal Başlık TR` biçiminde yazılır.

Eski bir çalışma klasörüyle yeniden EPUB üretildiğinde içe aktarıcının eklediği yapay ilk başlıklar otomatik gizlenir; bunun için Calibre'de elle düzenleme veya yeniden çeviri gerekmez.

Yeni içe aktarılan EPUB'larda görsellerin konumu Markdown içinde `epub-resource:` bağlantısıyla korunur. Eski import sürümünde yalnızca `[Resim: ...]` yer tutucusu varsa görselin özgün konumu geriye dönük olarak belirlenemez; bu projeler yeni klasöre tekrar içe aktarılmalıdır.

## Glossary

Glossary her çeviri isteğine zorunlu veri olarak eklenir. Çıktıda kaynak terim görülmesine rağmen Türkçe karşılık bulunmazsa program bir düzeltme isteği gönderir. İkinci sonuç da terimi içermiyorsa bölüm kaydedilmez ve hata gösterilir. Başarılı parçalar korunur.

Çalışan bölümün glossary ve ayarları checkpoint'e sabitlenir. Ayar veya glossary değişikliği bir sonraki bölümden itibaren uygulanır. Tamamlanmamış bölümün kaynak, prompt veya glossary değerini elle değiştirmek güvenlik hatası oluşturur.

## Context ve token koruması

Program önce LM Studio `/api/v1/models` endpoint'ini sorgular. Seçili model yüklüyse `loaded_instances[].config.context_length` kullanılır. Bilgi alınamazsa `fallback_context_length` değerine döner.

Yaklaşık giriş tokenı `karakter / 3` olarak hesaplanır. Çıktı token payı ve güvenlik payıyla birlikte yüklü context sınırını aşıyorsa istek gönderilmez. Bu özellik Ayarlar'dan kapatılabilir.

Kitabın yüzde 25, 50 ve 75 eşiklerinde yalnızca olay/bağlam özeti sıkıştırılır. Glossary, isimler, hitap ve üslup bölümleri ayrı tutulur ve sıkıştırma isteğine değiştirilmek üzere verilmez. Eşiklerin işlendiği checkpoint'te saklanır; yeniden açıldığında aynı eşik tekrarlanmaz.

## CLI kullanımı

```powershell
py -3 cevir.py --check
py -3 cevir.py --epub "Z:\Kitaplar\Book.epub"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --analyze
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --analyze-file "004-Begin-Reading.md"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --file "003-Chapter.md"
```

`BASLAT.cmd` mevcut etkileşimli CLI akışını çalıştırır. GUI ve CLI aynı `TranslationEngine` sınıfını kullandığı için çeviri ve checkpoint mantığı iki yerde çoğaltılmaz.

## EXE oluşturma

`BUILD.cmd` çalıştırılır. Komut bağımlılıkları yükler ve verilen sarı/siyah logoyu kullanan, PyInstaller ile tek dosyalı, konsolsuz `dist\Cevirgec.exe` üretir. `config.json` ve `translation_prompt.txt` dosyaları EXE'nin yanına kopyalanır; böylece ayarlar kalıcı olarak değiştirilebilir.

PyInstaller'ın tek dosya EXE'si ilk açılışta geçici klasöre açıldığı için başlangıç birkaç saniye sürebilir. Bazı antivirüs ürünleri imzasız, tek dosyalı PyInstaller uygulamalarına yanlış pozitif verebilir.

## Sınırlar

- DRM'li veya şifreli EPUB desteklenmez.
- Taranmış sayfa/OCR kitabı desteklenmez.
- Çok karmaşık tablolar ve EPUB'a özel etkileşimli içerikler sadeleşebilir.
- Bir model bitiş işaretini üretse bile anlamsal eksiksizlik ve çeviri kalitesi insan kontrolü gerektirir.
- Görev Yöneticisi veya işletim sistemiyle zorla sonlandırmayı uygulama engelleyemez. Son tamamlanan checkpoint korunur.

`__pycache__` ve `*.pyc` dosyaları çalışma zamanında Python tarafından oluşturulan önbellektir; kaynak veya teslim paketinin parçası değildir.
