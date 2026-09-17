# Çevirgeç — Ayrıntılı Kullanım Kılavuzu

Bu belge Çevirgeç'in kurulumu, arayüzü, ayarları, proje yapısı, EPUB üretimi, CLI kullanımı ve sorun giderme adımlarını kapsar. Kısa tanıtım ve hızlı başlangıç için [README.md](README.md) dosyasına bakın.

## İçindekiler

- [Kurulum ve çalıştırma](#kurulum-ve-çalıştırma)
- [LM Studio bağlantısı ve model seçimi](#lm-studio-bağlantısı-ve-model-seçimi)
- [Arayüz kullanımı](#arayüz-kullanımı)
- [Özel terim sözlüğü](#özel-terim-sözlüğü)
- [İsteğe bağlı ön analiz](#isteğe-bağlı-ön-analiz)
- [Çeviri, kontrol noktaları ve yeniden deneme](#çeviri-kontrol-noktaları-ve-yeniden-deneme)
- [Context ve token koruması](#context-ve-token-koruması)
- [Ayarlar](#ayarlar)
- [Proje klasörü ve dosyalar](#proje-klasörü-ve-dosyalar)
- [EPUB çıktısı](#epub-çıktısı)
- [CLI kullanımı](#cli-kullanımı)
- [Windows EXE oluşturma](#windows-exe-oluşturma)
- [Kaynak dosyaların görevleri](#kaynak-dosyaların-görevleri)
- [Testler](#testler)
- [Gizlilik ve güvenlik](#gizlilik-ve-güvenlik)
- [Sınırlamalar](#sınırlamalar)
- [Sorun giderme](#sorun-giderme)

## Kurulum ve çalıştırma

Gereksinimler:

- Python 3.10 veya üzeri
- [LM Studio](https://lmstudio.ai/) ve yüklü bir sohbet modeli
- Kaynak koddan arayüz için PySide6
- Paketleme için PyInstaller
- İsteğe bağlı EPUBCheck; JAR kullanılırsa Java

Windows PowerShell:

```powershell
py -3 -m pip install -r requirements.txt
GUI_BASLAT.cmd
```

Linux/macOS terminali:

```bash
python3 -m pip install -r requirements.txt
python3 gui.py
```

Windows'ta `GUI_BASLAT.cmd` arayüzü, `BASLAT.cmd` etkileşimli CLI akışını başlatır. Linux ve macOS için hazır paketleme dosyası yoktur; Python ve PySide6 ile kaynak koddan çalıştırılır.

## LM Studio bağlantısı ve model seçimi

LM Studio'da model yüklenmeli ve yerel sunucu başlatılmalıdır. Varsayılan adres:

```text
http://127.0.0.1:1234/v1
```

Çevirgeç model keşfi için önce `/api/v1/models`, gerektiğinde `/api/v0/models`; çeviri ve analiz istekleri için `/api/v1/chat` kullanır. Tek model yüklüyse otomatik seçilir. Birden fazla model varsa açılır listeden seçim yapılır; model alanı serbest metin değildir.

Ana ekranda seçilen modelin yanında LM Studio'da fiilen yüklü model ve algılanabiliyorsa aktif context uzunluğu gösterilir. **Yenile** düğmesi sunucu durumunu ve model listesini yeniden sorgular.

Base URL yalnızca `localhost`, `127.0.0.1` veya `::1` adreslerini kabul eder. LM Studio anahtar gerektirecek şekilde yapılandırıldıysa `LM_STUDIO_API_KEY` ortam değişkeni kullanılabilir.

## Arayüz kullanımı

Yeni bir çeviri için:

1. **EPUB dosyası** alanından kaynak kitabı seçin.
2. **Çalışma klasörü** alanından projenin oluşturulacağı ana klasörü seçin.
3. Gerekirse **Yenile** ile model listesini güncelleyin.
4. İsterseniz **Ön analiz yap** seçeneğini açın.
5. **Çeviriyi Başlat** düğmesine basın.
6. Kitaba özgü terimleri girin veya sözlüğü boş bırakın.
7. Ön analiz açıksa analiz edilecek hikâye bölümlerini seçin.

Uygulama `<KitapAdı>-ceviri` adlı proje klasörü oluşturur. Aynı ad kullanımdaysa numaralı yeni bir ad seçilir.

### Üst alan

- **Seçili model:** Çeviride kullanılacak yüklü model.
- **LM Studio:** Yüklü model ve algılanan context uzunluğu.
- **Yenile:** Model listesini ve sunucu durumunu yeniden sorgular.
- **EPUB dosyası:** Yeni projenin kaynak EPUB'u.
- **Çalışma klasörü:** Projenin oluşturulacağı ana klasör.
- **Projeyi sürdür:** Mevcut `<KitapAdı>-ceviri` klasörünü açar ve ilk eksik checkpoint'ten devam eder.
- **Terim düzenle:** Projenin sözlüğünü açar ve gerekirse etkilenen tamamlanmış bölümlerin yeniden çevrilmesini önerir.
- **Ayarlar:** Kitap/EPUB ve LM Studio/model seçeneklerini açar.

### Bölüm listesi ve sekmeler

Sol bölüm listesi EPUB içindekiler sırasını ve bölüm durumlarını gösterir. Bir bölüme tıklamak yalnızca kaynak ve çeviri önizlemesini açar; çeviri başlatmaz.

Başlıca durumlar:

- **Bekliyor:** Henüz başlanmadı.
- **Sıradaki:** İlk tamamlanmamış bölüm.
- **Ön analiz:** Analiz sürüyor.
- **Çevriliyor:** Bölüm parçaları çevriliyor.
- **Notlar hazırlanıyor:** Süreklilik notları üretiliyor.
- **Devam edecek:** Kaydedilmiş checkpoint var.
- **Tamamlandı:** Çeviri ve bölüm notları kaydedildi.

Sağ sekmeler:

- **Durum:** Aktif bölüm, parça sayısı ve ilerleme. Yüzde değeri çubuğun sağındaki sabit koyu kutuda gösterilir.
- **Konsol:** API istekleri, token bilgileri, tekrarlar, uyarılar ve hatalar.
- **Ön Analiz:** Kaydedilmiş analiz sonuçları.
- **Kaynak / Türkçe:** Seçilen bölümün iki metnini yan yana gösterir.

Konsol kayıtları aynı zamanda proje içindeki `translation.log` dosyasına yazılır.

### Duraklatma ve güvenli kapanış

**Duraklat** aktif LM Studio isteğini kesmez. İstek tamamlanır, geçerli sonuç checkpoint'e yazılır ve program sonraki parçaya geçmeden bekler. Düğme önce **Duraklatılıyor…**, ardından **Devam Et** olur.

Pencere kapatılırsa devam eden API isteği ile kayıt işleminin tamamlanması beklenir. Görev Yöneticisi veya işletim sistemiyle zorla sonlandırma engellenemez; bu durumda son tamamlanmış checkpoint korunur.

### Bir bölümü yeniden çevirme

Bölüme sağ tıklayıp **Bu bölümü yeniden çevir** seçilebilir. Mevcut çeviri `_python_translation/<bölüm>/recheck-prev-<tarih>.md` olarak yedeklenir. Bölüm `next` durumuna alınır ve sonraki bölümlerin bağlamı kullanılmadan yeniden çevrilir. Önceki bölümlerin terim, karakter ve üslup kararları korunur.

Yeniden çeviri yalnızca seçilen bölümü işler. Tamamlandığında `TAM-CEVIRI.md` ve kitap bütünü tamamlanmışsa EPUB yeniden oluşturulur. Aynı bölüm birden fazla kez yeniden çevrilebilir.

## Özel terim sözlüğü

Her kitap için İngilizce terim ve zorunlu Türkçe karşılığı eşleştirilebilir:

| İngilizce terim | Zorunlu karşılık |
|---|---|
| `Murderbot` | `Katilbot` |
| `SecUnit` | `GüvBirim` |

Sözlük isteğe bağlıdır ve `glossary.json` içinde proje bazında saklanır. Kaynağı mevcut çeviri parçasında geçen terimler denetlenir. Karşılık bulunmazsa modelden yalnızca terminolojiyi düzelten ikinci bir yanıt istenir.

Düzeltme yine başarısızsa varsayılan davranış ayrıntıyı `glossary_fixes.log` dosyasına yazıp daha az eksik sonuçla devam etmektir. **Katı sözlük** açıksa bölüm durur ve geçersiz sonuç kaydedilmez. Türkçe çekimli biçimler belirli ek ve ses değişimi kurallarıyla kabul edilir.

**Terim düzenle** ile terim eklenir, kaldırılır veya karşılığı değiştirilirse uygulama bu terimin geçtiği tamamlanmış bölümleri bulur ve yeniden çevirmeyi önerir. Henüz çevrilmemiş bölümler zaten güncel sözlüğü kullanır.

Çalışan bölümün sözlüğü checkpoint'e sabitlenir. Tamamlanmamış bölümün kaynak, prompt veya glossary dosyasını elle değiştirmek güvenlik hatasına neden olur.

## İsteğe bağlı ön analiz

Ön analiz, çeviriden önce seçilen bölümleri modele inceleterek karakter, terim, anlatım, değişim ve belirsizlik notları üretir. Zorunlu değildir ve toplam işlem süresini yaklaşık iki kata kadar uzatabilir.

Kapak, telif, yazar tanıtımı ve bülten gibi yardımcı bölümler yerine hikâye bölümlerinin seçilmesi önerilir. Sonuçlar `BOOK-ANALYSIS.md` ve `_python_analysis` altında saklanır.

Analiz bilgileri iki kapsamda tutulur:

- **Kitap geneli:** Zamandan ve olay örgüsünden bağımsız, birden fazla bölümde tutarlı biçimde doğrulanan kararlar.
- **Bölüm özelinde:** Ruh hâli, ilişki, rol, yerel üslup ve yalnızca o bölüm için geçerli ipuçları.

Tam olay özetleri doğrudan çeviri promptuna verilmez. Analiz önerileri kullanıcı sözlüğünü kendiliğinden değiştirmez; kullanıcı kabul ederse yalnızca kitap geneli kapsamındaki uygun terimler sözlüğe eklenir.

Ön analizde yapay bitiş işareti aranmaz. Tam ve geçerli JSON şeması tamamlanma ölçütüdür. Eksik/bozuk JSON veya geçici bağlantı hatası **Otomatik tekrar sayısı** kadar yeniden denenir. Tamamlanan analiz parçaları korunur.

## Çeviri, kontrol noktaları ve yeniden deneme

Her bölüm **Parça boyutu** değerine göre bölünür. Başarılı her parça atomik olarak hem state dosyasına hem ayrı parça dosyasına yazılır. Program kapanırsa tamamlanan parçalar yeniden üretilmez.

Aşağıdaki geçici veya doğrulanabilir sorunlar otomatik tekrar edilir:

- Eksik tamamlanma işareti
- Boş veya biçimsel olarak geçersiz yanıt
- Yanıta karışan araç/düşünme işaretleri
- Kaynak görsel, iç bağlantı, çapa veya tablo yapısının bozulması
- Geçici bağlantı ve timeout sorunları
- HTTP 408, 429 ve 5xx yanıtları

Context yetersizliği, çıktı token sınırı, geçersiz ayar, kaynak değişikliği ve checkpoint/prompt/sözlük uyuşmazlığı kullanıcı müdahalesi gerektirir ve otomatik tekrarlanmaz.

### Bitiş işareti ve işaretsiz sınır

Uzun çeviri yanıtlarında modelden görünmez bir tamamlanma işareti istenir. Model metni tamamlayıp işareti unutursa geçerli görünen sonuç reddedilebilir. **İşaretsiz sınır**, kaç kaynak karakterine kadar bu yapay işaretin aranmayacağını belirler.

Kural:

```text
kaynak parçanın kırpılmış karakter sayısı <= işaretsiz sınır
```

Bu değer token sayısı değildir. Konsoldaki `giriş`, `çıkış` ve `gerekli` değerleri; sistem promptu, sözlük, referans ve önceki çeviri gibi ek içerikleri de kapsadığı için işaretsiz sınır bunlardan hesaplanamaz.

| Değer | Davranış |
|---:|---|
| `400` | Yalnızca çok kısa parçalar işaretsiz kabul edilir. En katı varsayılandır. |
| `3000` | Orta uzunluktaki parçalar işaretsiz kabul edilir; gereksiz durmalar azalır. |
| `6000` | Varsayılan parça boyutundaki normal parçaların neredeyse tamamı işaretsiz kabul edilir. |
| Denetim kapalı | Hiçbir çeviri veya glossary düzeltme yanıtında işaret aranmaz. |

Sınırı yükseltmek, modelin düzgün görünen fakat anlamsal olarak yarım bıraktığı bir yanıtı fark etme güvencesini azaltır. Boş/bozuk çıktı, çıktı token sınırı, glossary ve EPUB yapısı kontrolleri ise çalışmaya devam eder.

Bitiş işareti, işaretsiz sınır ve otomatik tekrar sayısı; bu nedenle durmuş yarım bölüm **Projeyi sürdür** ile açıldığında checkpoint silinmeden güncellenir. Yeni değer ilk kaydedilmemiş parçadan itibaren uygulanır.

## Context ve token koruması

Program LM Studio'daki yüklü modelden gerçek context uzunluğunu okumaya çalışır. Bilgi alınamazsa **Fallback context** kullanılır.

Yaklaşık giriş hesabı:

```text
tahmini giriş + çıktı token payı + güvenlik payı <= context uzunluğu
```

Tahmini giriş yaklaşık `karakter / 3` yöntemiyle hesaplanır. Bu kesin tokenizer sayımı değildir; taşma riskini istek gönderilmeden önce fark etmek için koruyucu kontroldür.

Kitabın yaklaşık %25, %50 ve %75 noktalarında, ayrıca bir sonraki bölümün bağlam referansı context'in %40'ını aştığında geçmiş notlar sıkıştırılır. Önce kalıcı terim, isim ve hitap kararları `_python_translation/continuity-decisions.md` dosyasına ayrılır; ardından olay özeti kısaltılır. Sıkıştırılan bölüm adları `book-state.json` içinde tutulur ve yeniden açılışta aynı iş tekrarlanmaz.

## Ayarlar

Emin değilseniz varsayılan değerleri koruyun. **Varsayılan ayarlara dön** güvenli başlangıç değerlerini geri yükler.

| Grup | Ayar | İşlevi |
|---|---|---|
| Kitap ve EPUB | EPUBCheck | Üretilen EPUB üzerinde harici standart doğrulamasını açar. |
| Kitap ve EPUB | EPUBCheck yolu | `epubcheck.jar`, çalıştırılabilir dosya veya boşsa PATH içindeki `epubcheck`. |
| Kitap ve EPUB | Katı sözlük | Uygulanamayan zorunlu terimde çeviriyi durdurur. Varsayılan kapalıdır. |
| Kitap ve EPUB | Bitiş işareti denetimi | Uzun yanıtlarda yapay tamamlanma işareti aranmasını açar/kapatır. |
| Kitap ve EPUB | İşaretsiz sınır | Bu karakter uzunluğuna kadar ilk çeviri yanıtını işaretsiz kabul eder. Varsayılan `400`. |
| Kitap ve EPUB | Otomatik tekrar sayısı | İlk isteğe dahil olmayan ek deneme sayısı. Varsayılan `2`, minimum `1`. |
| LM Studio ve model | Base URL | Yerel LM Studio API adresi. |
| LM Studio ve model | Model | Yüklü modeller arasından kullanılacak model. |
| LM Studio ve model | Temperature | Modelin üretim çeşitliliği. |
| LM Studio ve model | Timeout | Tek API isteği için saniye cinsinden azami bekleme. |
| LM Studio ve model | Parça boyutu | Kaynağın yaklaşık karakter tabanlı parça büyüklüğü. |
| LM Studio ve model | Çıktı token payı | Çeviri yanıtına ayrılan azami token bütçesi. |
| LM Studio ve model | Not çıktı tokenı | Analiz ve bölüm notlarına ayrılan token bütçesi. |
| LM Studio ve model | Fallback context | LM Studio context bildirmezse kullanılacak sınır. |
| LM Studio ve model | Güvenlik payı | Context hesabında boş bırakılan token alanı. |
| LM Studio ve model | Token tahmini | Yaklaşık context ön kontrolünü açar/kapatır. |

Parça boyutu ve çoğu çalışma ayarı mevcut bölümün checkpoint'ine sabitlenir; değişiklikler sonraki bölümde uygulanır. Bitiş işareti ayarları ve otomatik tekrar sayısı yarım bölümde canlı güncellenebilen istisnalardır.

## Proje klasörü ve dosyalar

Tipik proje:

```text
Kitap-ceviri/
├── source/                    # EPUB'dan çıkarılan kaynak Markdown bölümleri
├── translation/               # Tamamlanan Türkçe bölümler
├── _python_translation/       # Parça checkpointleri ve çalışma durumu
├── _python_analysis/          # Ön analiz checkpointleri ve JSON kayıtları
├── 00-CONTEXT.md              # Bölüm durumları ve süreklilik notları
├── BOOK-ANALYSIS.md           # Okunabilir ön analiz sonucu
├── glossary.json              # Kitaba özel zorunlu terimler
├── glossary_fixes.log         # Uygulanamayan glossary düzeltmeleri
├── translation.log            # Konsol, hata ve token kayıtları
└── TAM-CEVIRI.md              # Birleştirilmiş çeviri
```

Çıktı EPUB aynı çalışma alanında oluşturulur. `_python_translation`, `_python_analysis` veya state dosyalarını silmek devam bilgisini kaybettirebilir. Kaynak, prompt ve glossary dosyalarını yarım bölüm sırasında elle değiştirmeyin.

## EPUB çıktısı

Kaynak EPUB değiştirilmez. Çıktı adı `<Orijinal Dosya Adı> TR.epub` biçimindedir; aynı ad varsa `TR-2`, `TR-3` olarak artırılır.

Çıktı oluşturulurken:

- Kaynak metadata korunur ve dil `tr` yapılır.
- ISBN varsa korunur; yoksa yapay ISBN üretilmez.
- Kapak, görsel, font ve diğer ikili varlıklar kopyalanır.
- Yüzde kodlanmış ve parantezli varlık yolları çözümlenir.
- Basit tablolar yeniden XHTML tabloya dönüştürülür.
- Dipnotlar ve iç bağlantılar yeni bölüm/çapa hedeflerine bağlanır.
- Kaynaktaki kapak kullanılır; ikinci sentetik kapak oluşturulmaz.
- EPUB3 `nav.xhtml` ve EPUB2 uyumlu `toc.ncx` üretilir.
- Kaynakta görünür başlık yoksa yapay `<h1>` eklenmez.
- `mimetype`, ZIP sırası, container, OPF manifest/spine, XML, görseller ve yerel bağlantılar dahili olarak doğrulanır.

### Yapısal EPUB işaretleri

Ara Markdown biçiminde:

- Görseller `epub-resource:`
- İç bağlantılar `epub-link:`
- Hedef çapalar `[[EPUB_ANCHOR:...]]`

ile korunur. Model bağımsız bir çapa bloğunu düşürür ve görünür paragraf düzenini korursa çapa kaynak paragraf konumuna otomatik geri yerleştirilir. Görsel veya bağlantı hedefi değişirse, çapa konumu belirsizse ya da model farklı/fazladan hedef üretirse sonuç reddedilir ve otomatik yeniden denenir.

Yalnızca teknik başlık ile görsel taşıyan kapak/harita bölümleri modele gönderilmeden korunur. Eski import sürümünde kaybolmuş dipnot veya bağlantı bilgisi geriye dönük çıkarılamaz; gerekirse kaynak EPUB yeni proje olarak içe aktarılmalıdır.

### İsteğe bağlı EPUBCheck

**EPUBCheck** açılırsa dahili doğrulamadan sonra harici standart denetimi çalışır. **EPUBCheck yolu** alanına resmi `epubcheck.jar` veya çalıştırılabilir dosya yazılabilir; boşsa PATH içindeki `epubcheck` aranır. JAR için Java PATH içinde olmalıdır.

EPUBCheck hata verirse oluşturulan EPUB silinmez. Rapor Konsol ve `translation.log` içine yazılır, işlem hata olarak bildirilir.

## CLI kullanımı

Windows:

```powershell
py -3 cevir.py --check
py -3 cevir.py --epub "Z:\Kitaplar\Book.epub"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --file "003-Chapter.md"
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --analyze
py -3 cevir.py --book "Z:\Kitaplar\Book-ceviri" --analyze-file "004-Begin-Reading.md"
```

Linux/macOS'ta `py -3` yerine `python3` kullanılır.

| Seçenek | Açıklama |
|---|---|
| `--epub DOSYA` | EPUB'u içe aktarır ve oluşan projeyi çevirir. |
| `--book KLASÖR` | Mevcut projeyi sürdürür. |
| `--file DOSYA` | Projedeki tek kaynak bölümünü işler. |
| `--check` | LM Studio ve model bağlantısını kontrol eder. |
| `--analyze` | Çeviriden önce ön analiz çalıştırır. |
| `--analyze-file DOSYA` | Analize dahil edilecek dosyayı belirtir; tekrar kullanılabilir. |

GUI ve CLI aynı `TranslationEngine` sınıfını kullanır; çeviri ve checkpoint mantığı iki ayrı yerde çoğaltılmaz.

## Windows EXE oluşturma

1. Proje klasöründe `BUILD.cmd` çalıştırılır.
2. Sabitlenmiş PySide6/PyInstaller bağımlılıkları yüklenir.
3. `dist\Cevirgec.exe` oluşturulur.
4. `config.json` ve `translation_prompt.txt` EXE'nin yanına kopyalanır.

PyInstaller tek dosyalı EXE ilk açılışta geçici klasöre açıldığı için başlangıç birkaç saniye sürebilir. İmzasız PyInstaller uygulamaları bazı antivirüs ürünlerinde yanlış pozitif oluşturabilir. Linux ve macOS için hazır paketleme komut dosyası sağlanmaz.

## Kaynak dosyaların görevleri

| Dosya | Görevi |
|---|---|
| `gui.py` | PySide6 arayüzü ve kullanıcı etkileşimi |
| `app_core.py` | Ayarlar, LM Studio istemcisi, çeviri/analiz/checkpoint çekirdeği |
| `epub_import.py` | EPUB'u proje ve Markdown bölümlerine dönüştürme |
| `epub_output.py` | Çevrilmiş EPUB'u üretme ve doğrulama |
| `cevir.py` | CLI giriş noktası |
| `translation_prompt.txt` | Modelin çeviri sistem talimatı |
| `config.json` | Kalıcı kullanıcı ayarları |
| `requirements.txt` | Sabitlenmiş çalışma ve derleme bağımlılıkları |
| `tests/test_core.py` | Çekirdek, checkpoint ve EPUB testleri |

## Testler

Windows:

```powershell
py -3 -m unittest discover -s tests -v
```

Linux/macOS:

```bash
python3 -m unittest discover -s tests -v
```

Testler gerçek bir kitabın tamamını modele çevirtmez. Bölme, doğrulama, glossary, checkpoint, tekrar, analiz, EPUB içe aktarma ve çıktı oluşturmanın programatik davranışlarını denetler. Gerçek çeviri kalitesi kullanılan model, quantization ve ayarlara bağlıdır.

## Gizlilik ve güvenlik

- Kitap metni yalnızca yapılandırılmış yerel LM Studio adresine gönderilir.
- Uygulama kendi başına bulut çeviri hizmetine bağlanmaz.
- Base URL yerel adreslerle sınırlandırılmıştır.
- API isteklerinde sistem proxy'si kullanılmaz.
- Kaynak EPUB değiştirilmez.
- `config.json` içine parola veya erişim anahtarı yazmayın.
- Hata kaydı paylaşmadan önce kişisel klasör yollarını ve kitap adlarını kontrol edin.

OpenRouter veya başka bir uzak servis LM Studio dışında proxy/geçit olarak kullanılıyorsa metnin gizliliği o servisin koşullarına bağlıdır; Çevirgeç'in varsayılan yerel Base URL kısıtı bu kullanım için değiştirilmemiştir.

## Sınırlamalar

- DRM'li veya şifreli EPUB desteklenmez.
- Taranmış sayfalara OCR uygulanmaz.
- `rowspan`/`colspan`, karmaşık CSS, şiirsel boşluklar, JavaScript ve etkileşimli öğeler sadeleşebilir.
- Kaynak stil dosyaları kopyalansa da çevrilmiş sayfalar sade çıktı CSS'i kullanır; yayınevi tasarımı birebir çoğaltılmaz.
- Tamamlanma işareti teknik bir kontroldür; anlamsal eksiksizliği kanıtlamaz.
- Yerel model hatalı çeviri, atlama veya uydurma üretebilir.
- Son EPUB'un Calibre ve en az bir farklı okuyucuda kontrol edilmesi önerilir.

## Sorun giderme

| Belirti | Kontrol edilecekler |
|---|---|
| Model görünmüyor | LM Studio sunucusunu ve modelin gerçekten yüklü olduğunu doğrulayın; **Yenile**'ye basın. |
| Bağlantı reddedildi | Base URL ve LM Studio portunu kontrol edin. Port işletim sistemi tarafından ayrılmışsa başka bir yerel port kullanın. |
| Context yetersiz | Parça boyutunu veya çıktı token payını azaltın; doğru modelin yüklü olduğunu doğrulayın. |
| Yanıt tamamlanma işareti taşımıyor | Kaynak parça işaretsiz sınırdan uzundur ve model işareti yazmamıştır. Geçici olarak sınırı kaynak parça uzunluğunun üzerine çıkarın veya denetimi kapatın. `giriş/çıkış` token değerlerinden kaynak karakter uzunluğu hesaplanamaz. Varsayılan parça boyutu `6000` ise sınırı `6000` yapmak normal parçaların çoğunu işaretsiz kabul eder ancak eksik çeviriyi yakalama güvencesini azaltır. |
| Ön analiz JSON hatası | Tekrarlar bittikten sonra sürüyorsa model ve temperature değerini kontrol edin. |
| Sözlük karşılığı eksik | `glossary_fixes.log` içeriğini kontrol edin. Terimi veya karşılığı düzeltin; gerekirse etkilenen bölümü yeniden çevirin. |
| Proje uyuşmazlığı | Kaynak, prompt, glossary veya checkpoint dosyalarını yarım bölüm sırasında elle değiştirmeyin. |
| Görseller eski projede bozuk | Kaynak EPUB'u yeni proje klasörüne tekrar içe aktarın. |
| Yapısal EPUB işaretleri korunmadı | Güvenli bağımsız çapa eksikliği otomatik düzeltilir. Hata sürüyorsa model görsel/bağlantı hedefini değiştirmiş, farklı/fazladan çapa üretmiş veya paragraf yapısını belirsizleştirmiştir. |
| EPUBCheck yolu bulunamadı | Resmî `epubcheck.jar`/EXE yolunu yazın; JAR için Java'yı PATH'e ekleyin. |
| EPUBCheck hata verdi | Konsol ve `translation.log` raporunu inceleyin. Oluşturulan EPUB silinmez. |
| DRM/şifreleme hatası | DRM içermeyen ve kullanma hakkına sahip olduğunuz bir EPUB kullanın. |

`__pycache__` ve `*.pyc` dosyaları Python tarafından oluşturulan geçici önbellektir; kaynak veya dağıtım paketinin parçası değildir.
