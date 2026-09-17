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

**Durum** sekmesindeki yüzde, sarı ilerleme dolgusunun içine yazılmaz. Çubuğun sağındaki koyu zeminli ayrı kutuda gösterildiği için %25, %50 ve %100 değerlerinde aynı kontrastla okunur.

Ön analiz kitabı çeviriden önce parçalar halinde inceleyerek karakter, terim, üslup, değişim ve belirsizlik notları üretir. Seçeneğin araç ipucunda da belirtildiği gibi toplam işlem süresini iki kata kadar uzatabilir. Sonuç **Ön Analiz** sekmesinde ve `BOOK-ANALYSIS.md` dosyasında görülür. Analiz önerileri `glossary.json` dosyasını değiştirmez; kullanıcının zorunlu sözlüğü her zaman önceliklidir.

Analiz bilgileri iki kapsama ayrılır. Model yalnızca zamandan ve olay örgüsünden bağımsız bilgileri kitap geneli adayı olarak işaretler. Motor bu adayları en az iki ayrı bölümde aynı biçimde görülmeleri ve terim karşılıklarının çelişmemesi halinde kitap geneline yükseltir. Karakterin o bölümdeki ruh hâli, konuşma sesi, ilişki/rol değişimi ve yerel üslup yalnızca ilgili bölümün çeviri isteğine eklenir. Tam olay özetleri hiçbir çeviri prompt'una verilmez. Böylece sonraki bölümde öğrenilen geçici bir karakter özelliğinin önceki veya başka bir bölüme taşınma riski azaltılır; yine de ön analiz bir LLM çıktısı olduğu için insan doğrulamasının yerini tutmaz.

Bir bölümün çevirisi başladıktan sonra kullanılan referans o bölümün checkpoint'ine sabitlenir. Yeni ön analiz veya değişen analiz sonuçları devam eden bölümün ortasında uygulanmaz.

Ön analiz raporu ilk tamamlanan analiz parçasından itibaren **Ön Analiz** sekmesinde güncellenir. Sol bölüm listesindeki durumlar da aktif checkpoint'e göre `Ön analiz`, `Çevriliyor`, `Notlar hazırlanıyor`, `Devam edecek` veya `Tamamlandı` olarak yenilenir.

Ön analizde eksik veya bozuk model yanıtı alınırsa aynı parça en fazla iki kez otomatik yeniden denenir. Üç denemenin tamamı başarısız olursa işlem durur; tamamlanan parçalar korunur ve proje yeniden başlatıldığında ilk eksik parçadan devam edilir. Context hesabı, ayar veya checkpoint uyuşmazlığı gibi yapılandırma hataları otomatik tekrarlanmaz. Ön analizde yapay bir bitiş işareti aranmaz: tam ve geçerli JSON şeması tamamlanma ölçütüdür. Eksik alanlı, bozuk veya token sınırına ulaşmış yanıt checkpoint'e yazılmaz. Normal çeviri parçalarında bitiş işareti denetimi varsayılan olarak açıktır; varsayılan `400` karakterlik işaretsiz sınırın üstünde işaret zorunludur.

Çeviri sırasında tamamlanma işareti, boş/geçersiz yanıt, tool/düşünce işareti, zorunlu glossary, EPUB görsel/iç bağlantı/çapa hedefi uyuşmazlığı veya geçici LM Studio bağlantı/HTTP hatası oluşursa aynı kaynak parça en fazla iki kez otomatik yeniden denenir. **Ayarlar → Bitiş işareti denetimi** açıkken **İşaretsiz sınır** kadar veya daha kısa ilk çeviri parçalarında işaret aranmaz; varsayılan sınır `400`, istenirse örneğin `500` yapılabilir. Denetim tamamen kapatılırsa çeviri ve glossary düzeltme yanıtlarının hiçbirinde işaret aranmaz. Boş/bozuk çıktı, token sınırı, glossary ve EPUB yapısı denetimleri her durumda uygulanır. Bitiş denetimini tamamen kapatmak modelin anlamsal olarak yarım bıraktığı bir metni fark etme güvencesini azaltır. Hatalı yanıt hiçbir zaman parça checkpoint'ine eklenmez. Çıktı token sınırı, context sınırı, ayar ve checkpoint uyuşmazlıkları otomatik tekrarlanmaz; bunlar kullanıcı müdahalesi gerektirir.

Bu iki bitiş işareti ayarı, daha önce bu nedenle durmuş yarım bir bölüm **Projeyi sürdür** ile yeniden çalıştırıldığında mevcut checkpoint silinmeden uygulanır. Önceden kaydedilmiş parçalar korunur; yeni değer yalnızca ilk kaydedilmemiş parçadan itibaren geçerlidir.

Duraklatma, devam eden LM Studio isteğini yarıda kesmez. Yanıt doğrulanıp checkpoint'e yazıldıktan sonra uygulanır. Pencere kapatılırsa program yine mevcut isteğin bitmesini ve checkpoint'in kaydedilmesini bekler. İstek süresiz beklemesin diye Ayarlar menüsündeki timeout kullanılır.

Var olan bir proje sağ üstteki **Projeyi sürdür** düğmesiyle sürdürülebilir. Bu düğme daha önce oluşturulan `{KitapAdı}-ceviri` klasörünü doğrudan açar; kaynak EPUB'u yeniden içe aktarmadan tamamlanan parçaları ve checkpointleri kullanır. **Ayarlar** düğmesi de sağ üsttedir. Bölüm ağacından bir kayda tıklamak yalnızca kaynak ve çeviri önizlemesini açar; tekrar çeviri başlatmaz. Bir bölüme **sağ tık → “Bu bölümü yeniden çevir”** ile o bölüm tek başına yeniden çevrilir: mevcut çeviri `_python_translation/<bölüm>/recheck-prev-<tarih>.md` olarak yedeklenir, bölüm `next` durumuna alınır ve çeviri isteğine sonraki bölümlerin özet/notları verilmez. Yeniden çeviri bitince sonraki bölümlere otomatik geçilmez; `TAM-CEVIRI.md` ve (kitap tamamsa) EPUB yenilenir. Yarım kalmış bir bölüm varsa kaydı yeni bağlama taşınır, çeviri kaldığı yerden sürer.

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
- Yüzde kodlanmış ve adında parantez bulunan görsel yollarını korur.
- Basit tabloları Markdown tablo üzerinden yeniden XHTML tabloya dönüştürür.
- Dipnot ve EPUB içi çapraz bağlantıları yeni bölüm dosyalarındaki hedef çapalarla yeniden bağlar.
- Kaynakta zaten spine/TOC içinde bulunan kapak bölümünü kullanır; ikinci bir sentetik kapak sayfası eklemez.
- TOC etiketini görünür sayfa başlığına dönüştürmez. Kaynakta görünür başlık yoksa kapak, başlık sayfası ve bölüm gövdesine yapay `<h1>` eklemez.
- Yeni EPUB3 `nav.xhtml` ve EPUB2 uyumluluğu için `toc.ncx` oluşturur.
- İçindekiler başlığında çeviri Markdown dosyasındaki ilk `#` başlığını kullanır.
- Başlık bulunamazsa bölüm dosya adına döner.
- Metadata'da `subtitle` olarak işaretlenmiş başlık varsa onun, yoksa ilk `dc:title` değerinin sonuna `TR` eklenir.
- Her üretimde mimetype/ZIP sırası, container, OPF manifest/spine, XML, görsel ve yerel bağlantı hedeflerini dahili olarak doğrular.

Eski bir çalışma klasörüyle yeniden EPUB üretildiğinde içe aktarıcının eklediği yapay ilk başlıklar otomatik gizlenir; bunun için Calibre'de elle düzenleme veya yeniden çeviri gerekmez.

Yeni içe aktarılan EPUB'larda görsellerin konumu Markdown içinde `epub-resource:`, iç bağlantılar `epub-link:` ve hedef çapalar `[[EPUB_ANCHOR:...]]` belirteçleriyle korunur. Model bunlardan birini değiştirirse parça kaydedilmez. Yalnızca teknik bölüm başlığı ile görsel içeren kapak/harita sayfaları modele gönderilmeden doğrudan korunur.

Tablo ve iç bağlantı eşlemesi import sürümü 4 ile oluşturulur. Eski import sürümünde kaybolmuş görsel/dipnot bilgisi geriye dönük çıkarılamaz; bu özellik gerekiyorsa kaynak EPUB yeni bir proje klasörüne tekrar içe aktarılmalıdır. Basit tablolar korunur; `rowspan`/`colspan`, karmaşık CSS düzenleri, JavaScript ve etkileşimli EPUB öğeleri sadeleşebilir.

### İsteğe bağlı EPUBCheck

**Ayarlar → EPUBCheck** ile harici standart doğrulaması açılabilir. **EPUBCheck yolu** alanına `epubcheck.jar` veya EPUBCheck çalıştırılabilir dosyasının yolu yazılır; alan boşsa PATH içindeki `epubcheck` aranır. JAR için Java'nın PATH içinde olması gerekir. Doğrulama çıktısı Konsol ve `translation.log` dosyasına yazılır. Hata halinde üretilen EPUB korunur ancak işlem hata olarak bildirilir.

Ayarlar penceresinde **EPUBCheck**, **EPUBCheck yolu** ve **Katı sözlük** seçenekleri **Kitap ve EPUB** grubundadır. Bunların altındaki ayrı **LM Studio ve model** grubunda **Base URL**, model, üretim/context ayarları, **Bitiş işareti denetimi** ve **İşaretsiz sınır** bulunur. Her iki bitiş ayarının tooltip'i etkisini ve kapatıldığında hangi kontrollerin çalışmaya devam ettiğini açıklar.

## Glossary

Glossary her çeviri isteğine zorunlu veri olarak eklenir. Karşılık denetimi yalnızca kaynağı o parçada geçen terimler için yapılır: kaynak parçada terim görülmesine rağmen Türkçe karşılık bulunmazsa program bir düzeltme isteği gönderir. İkinci sonuç da terimi içermiyorsa varsayılan davranış ayrıntıyı `glossary_fixes.log` dosyasına yazıp daha az eksik olan çıktıyla devam etmektir; **Ayarlar → Katı sözlük** açıksa bölüm kaydedilmez ve hata gösterilir. Kaynağı parçada hiç geçmeyen bir terimin karşılığı o parçada aranmaz. Başarılı parçalar korunur.

Çalışan bölümün glossary ve çoğu ayarı checkpoint'e sabitlenir. Ayar veya glossary değişikliği bir sonraki bölümden itibaren uygulanır. Yalnızca bitiş işareti denetimi ve işaretsiz sınır, yarım bölüm yeniden sürdürüldüğünde güncel ayarlardan alınır. Tamamlanmamış bölümün kaynak, prompt veya glossary değerini elle değiştirmek güvenlik hatası oluşturur.

**Terim düzenle** düğmesi, yalnızca yeni projede değil sürdürülen projede de sözlüğü açar. Bir terim eklendiğinde, kaldırıldığında veya karşılığı değiştiğinde uygulama o terimin geçtiği çevrilmiş (`done`) bölümleri tarar ve bunları yeniden çevirmeyi önerir. Onaylanırsa bu bölümler güvenli yeniden çeviriyle işlenir: sonraki bölümlerin bağlamı (özet/notlar) kullanılmaz, önceki bölümlerin kararları korunur. Değişen terim hiçbir bölümde geçmiyorsa yeniden çeviri gerekmez.

## Context ve token koruması

Program önce LM Studio `/api/v1/models` endpoint'ini sorgular. Seçili model yüklüyse `loaded_instances[].config.context_length` kullanılır. Bilgi alınamazsa `fallback_context_length` değerine döner.

Yaklaşık giriş tokenı `karakter / 3` olarak hesaplanır. Çıktı token payı ve güvenlik payıyla birlikte yüklü context sınırını aşıyorsa istek gönderilmez. Bu özellik Ayarlar'dan kapatılabilir.

Kitabın yüzde 25, 50 ve 75 eşiklerinde ve bağlam referansı context'in %40'ını aşınca notlar sıkıştırılır. Önce terim/isim/hitap kararları `continuity-decisions.md` dosyasına ayıklanır, sonra olay özeti kısaltılır. Hangi bölüm notlarının sıkıştırıldığı dosya adıyla `book-state.json` içinde saklanır; yeniden açıldığında tekrarlanmaz. Glossary karşılığı uygulanamazsa varsayılan olarak `glossary_fixes.log` dosyasına yazılır ve çeviri devam eder (Ayarlar → Katı sözlük ile durdurulabilir).

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

`BUILD.cmd` çalıştırılır. Komut `requirements.txt` içinde kesin sürümle sabitlenmiş PySide6 ve PyInstaller bağımlılıklarını yükler ve verilen sarı/siyah logoyu kullanan, PyInstaller ile tek dosyalı, konsolsuz `dist\Cevirgec.exe` üretir. `config.json` ve `translation_prompt.txt` dosyaları EXE'nin yanına kopyalanır; böylece ayarlar kalıcı olarak değiştirilebilir.

PyInstaller'ın tek dosya EXE'si ilk açılışta geçici klasöre açıldığı için başlangıç birkaç saniye sürebilir. Bazı antivirüs ürünleri imzasız, tek dosyalı PyInstaller uygulamalarına yanlış pozitif verebilir.

## Sınırlar

- DRM'li veya şifreli EPUB desteklenmez.
- Taranmış sayfa/OCR kitabı desteklenmez.
- Çok karmaşık tablolar ve EPUB'a özel etkileşimli içerikler sadeleşebilir.
- Bir model bitiş işaretini üretse bile anlamsal eksiksizlik ve çeviri kalitesi insan kontrolü gerektirir.
- Görev Yöneticisi veya işletim sistemiyle zorla sonlandırmayı uygulama engelleyemez. Son tamamlanan checkpoint korunur.

`__pycache__` ve `*.pyc` dosyaları çalışma zamanında Python tarafından oluşturulan önbellektir; kaynak veya teslim paketinin parçası değildir.
