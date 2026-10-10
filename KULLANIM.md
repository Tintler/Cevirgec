# Çevirgeç — Ayrıntılı Kullanım Kılavuzu

Bu belge Çevirgeç'in kurulumu, arayüzü, ayarları, proje yapısı, EPUB üretimi, CLI kullanımı ve sorun giderme adımlarını kapsar. Kısa tanıtım ve hızlı başlangıç için [README.md](README.md) dosyasına bakın.

## İçindekiler

- [Kurulum ve çalıştırma](#kurulum-ve-çalıştırma)
- [LM Studio bağlantısı ve model seçimi](#lm-studio-bağlantısı-ve-model-seçimi)
- [Arayüz kullanımı](#arayüz-kullanımı)
- [Okuma](#okuma)
- [Sesli okuma](#sesli-okuma)
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
- Kaynak koddan arayüz için pywebview ve arayüzü derlemek için Node.js 20+
- Windows'ta Microsoft Edge WebView2 (Windows 10/11'de hazır gelir)
- Paketleme için PyInstaller
- İsteğe bağlı EPUBCheck; JAR kullanılırsa Java

Windows PowerShell:

```powershell
py -3 -m pip install -r requirements.txt
GUI_BASLAT.cmd
```

`GUI_BASLAT.cmd` ilk açılışta `web` klasöründeki arayüzü derler (`npm ci` ve `npm run build`). Arayüz kodu değiştiğinde yeniden derlemek için `web` klasöründe `npm run build` çalıştırın.

```powershell
# Arayüz geliştirme: Vite sunucusu ile anlık yenileme
cd web; npm run dev
py -3 desktop.py --dev
```

Linux/macOS terminali:

```bash
python3 -m pip install -r requirements.txt
(cd web && npm ci && npm run build)
python3 desktop.py
```

Windows'ta `GUI_BASLAT.cmd` arayüzü, `BASLAT.cmd` etkileşimli CLI akışını başlatır. Linux ve macOS için hazır paketleme dosyası yoktur; Python, pywebview ve Node.js ile kaynak koddan çalıştırılır.

## LM Studio bağlantısı ve model seçimi

LM Studio'da model yüklenmeli ve yerel sunucu başlatılmalıdır. Varsayılan adres:

```text
http://127.0.0.1:1234/v1
```

Çevirgeç model keşfi için önce `/api/v1/models`, gerektiğinde `/api/v0/models`; çeviri ve analiz istekleri için `/api/v1/chat` kullanır. Tek model yüklüyse otomatik seçilir. Birden fazla model varsa açılır listeden seçim yapılır; model alanı serbest metin değildir.

Sol alttaki LM Studio kartında bağlantı durumu, seçili model ve algılanabiliyorsa aktif context uzunluğu gösterilir. Karttaki yenile düğmesi sunucu durumunu ve model listesini yeniden sorgular.

Base URL yalnızca `localhost`, `127.0.0.1` veya `::1` adreslerini kabul eder. LM Studio anahtar gerektirecek şekilde yapılandırıldıysa `LM_STUDIO_API_KEY` ortam değişkeni kullanılabilir.

## Arayüz kullanımı

Yeni bir çeviri için:

1. **Çeviri** sekmesinde **EPUB aç** ile kaynak kitabı seçin.
2. Çalışma klasörü varsayılan olarak EPUB'un bulunduğu klasördür; değiştirmek için kitap kartındaki klasör yoluna tıklayın.
3. Sol alttaki LM Studio kartında modelin yüklü olduğunu kontrol edin.
4. İsterseniz alt çubuktaki **Ön analiz** anahtarını açın.
5. **Çeviriyi başlat** düğmesine basın.
6. Kitaba özgü terimleri girin veya sözlüğü boş bırakın.
7. Ön analiz açıksa analiz edilecek hikâye bölümlerini seçin.

Uygulama `<KitapAdı>-ceviri` adlı proje klasörü oluşturur. Aynı ad kullanımdaysa numaralı yeni bir ad seçilir.

### Ekran düzeni

- **Sol kenar çubuğu:** **Çeviri**, **Okuma** ve **Ayarlar** sekmeleri; tema düğmesi ve LM Studio durum kartı.
- **Kitap kartı:** Kapak, başlık, yazar, bölüm/karakter sayısı, kaynak EPUB ve çalışma klasörü yolları ile tamamlanan bölüm ve yüzde. Yollara tıklayarak EPUB veya çalışma klasörü değiştirilebilir.
- **EPUB aç:** Yeni projenin kaynak EPUB'unu seçer.
- **Projeyi sürdür:** Mevcut `<KitapAdı>-ceviri` klasörünü açar ve ilk eksik checkpoint'ten devam eder.
- **Terim sözlüğü** (alt çubuk): Projenin sözlüğünü açar ve gerekirse etkilenen tamamlanmış bölümlerin yeniden çevrilmesini önerir.
- **Ayarlar** sekmesi: Görünüm, LM Studio/model, kitap/EPUB ve gelişmiş seçenekler.

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

- **Durum:** Aşama, aktif bölüm ve parça, tamamlanan bölüm, son kayıt zamanı ve ilerleme. Kitap geneli yüzde alt çubukta da gösterilir.
- **Konsol:** API istekleri, token bilgileri, tekrarlar, uyarılar ve hatalar.
- **Ön analiz:** Kaydedilmiş analiz sonuçları.
- **Kaynak / Türkçe:** Seçilen bölümün iki metnini yan yana gösterir; sözlük terimleri vurgulanır.

Konsol kayıtları aynı zamanda proje içindeki `translation.log` dosyasına yazılır.

### Duraklatma ve güvenli kapanış

**Duraklat** aktif LM Studio isteğini kesmez. İstek tamamlanır, geçerli sonuç checkpoint'e yazılır ve program sonraki parçaya geçmeden bekler. Düğme önce **Duraklatılıyor…**, ardından **Devam et** olur.

Pencere kapatılırsa devam eden API isteği ile kayıt işleminin tamamlanması beklenir. Görev Yöneticisi veya işletim sistemiyle zorla sonlandırma engellenemez; bu durumda son tamamlanmış checkpoint korunur.

### Bir bölümü yeniden çevirme

Bölüme sağ tıklayıp **Bu bölümü yeniden çevir** seçilebilir. Mevcut çeviri `_python_translation/<bölüm>/recheck-prev-<tarih>.md` olarak yedeklenir. Bölüm `next` durumuna alınır ve sonraki bölümlerin bağlamı kullanılmadan yeniden çevrilir. Önceki bölümlerin terim, karakter ve üslup kararları korunur.

Yeniden çeviri yalnızca seçilen bölümü işler. Tamamlandığında `TAM-CEVIRI.md` ve kitap bütünü tamamlanmışsa EPUB yeniden oluşturulur. Aynı bölüm birden fazla kez yeniden çevrilebilir.

## Okuma

**Okuma** sekmesi kendi EPUB kitaplarınızı okumak içindir; çeviri projelerinden bağımsızdır.

- **Kitap ekle** ile bir veya birden çok EPUB seçin ya da dosyaları pencerenin herhangi bir yerine sürükleyip bırakın. Dosyalar kopyalanmaz; kitaplık yalnızca konumlarını, okuma ilerlemesini ve yer imlerini `library.json` dosyasında (`config.json` ile aynı klasörde) saklar. DRM korumalı kitaplar eklenemez.
- Kapağa tıklayınca kitap kaldığı yerden açılır. Kitaplığın üstündeki **Okumaya devam et** alanı en son okunan, bitmemiş kitabı gösterir. Kitabı kaldırmak için kapağın köşesindeki × düğmesini ya da sağ tık menüsünü kullanın; dosyanın kendisi silinmez. Taşınmış veya silinmiş dosyalar soluk gösterilir.
- Okuyucuda soldaki panel **İçindekiler** ve **Yer imleri** arasında geçiş yapar. Üstteki yer imi düğmesi bulunduğunuz konumu işaretler veya işareti kaldırır.
- **Görünüm** (T simgesi) ile yazı boyutu, yazı tipi (serif/sans), satır aralığı, sütun genişliği ve sayfa rengi (tema, koyu, sepya, açık) seçilir; tercih tüm kitaplar için saklanır.
- Bölümler arasında alttaki oklarla, bölüm sonundaki **Sonraki bölüm** düğmesiyle ya da `Alt+←` / `Alt+→` ile geçilir. Alt çubuk kitap genelindeki ilerlemeyi gösterir.
- Kitaplar uygulamanın kendi yazı tipi ve renkleriyle gösterilir. Yayıncının CSS'i, betikleri ve dış kaynakları uygulanmaz; içerik güvenli bir HTML alt kümesine dönüştürülür. Kitap içindeki web bağlantıları sistem tarayıcısında açılır.

## Sesli okuma

Okuyucu, açık kitabı yerel bir **ses motoruyla** sesli okuyabilir; metin bilgisayardan çıkmaz. Motorlar tak-çıkar biçimdedir ve kitabın diline göre seçilir:

| Motor | Diller | Paket | Not |
|---|---|---|---|
| [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) | Türkçe | ~200 MB | Çok doğal Türkçe; model (~34 MB) ilk kullanımda iner. Apache-2.0. |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | Türkçe, İngilizce (ve daha fazlası eklenebilir) | ~70 MB | Hafif; her ses (~63 MB) ilk kullanımda iner. |

### Motor kurulumu

**Dağıtılan sürümde (EXE):** **Ayarlar → Sesli okuma** bölümünde motorun yanındaki **İndir ve kur** düğmesi paketi GitHub Releases'ten indirip programın klasöründeki `ses-motorlari\<motor>\` altına açar. İnternetsiz kurulum için paket zip'i **Dosyadan kur** ile seçilebilir. Bir motoru yeniden kurmak (güncellemek) indirilmiş modelleri ve sesleri korur; **Kaldır** motoru ve indirdiği dosyaları siler.

Program `C:\Program Files` gibi yazma izni olmayan bir klasördeyse motor kurulamaz; Çevirgeç'i yazılabilir bir klasöre taşıyın. Klasör yolu çok uzunsa (Windows 260 karakter sınırı) EMA modeli indirilemeyebilir; kısa bir yol (ör. `C:\Cevirgec`) kullanın.

**Kaynak koddan çalışırken:** Motorların Python paketleri kuruluysa doğrudan onlar kullanılır, zip gerekmez:

```powershell
py -3 -m pip install -r requirements-tts.txt
```

Bu komut PyTorch'un CPU sürümünü de kurar. EMA'nın NVIDIA ekran kartıyla çalışması için önce CUDA'lı PyTorch'u kurun (RTX 50 serisi dahil; farklı sürüm gerekirse komutu [pytorch.org](https://pytorch.org/get-started/locally/) sayfasından alın):

```powershell
py -3 -m pip install torch --index-url https://download.pytorch.org/whl/cu128
```

Dağıtılan EMA paketi yalnızca CPU içerir; model küçük olduğundan CPU'da da gerçek zamandan çok daha hızlıdır.

### Ses seçimi

**Ayarlar → Sesli okuma**'da her dil için hangi motor ve sesin okuyacağı seçilir (ör. Türkçe kitaplar → EMA Lightning, İngilizce kitaplar → Piper · Lessac). Seçim yapılmazsa Türkçe için EMA, diğer diller için o dili destekleyen ilk motor kullanılır. Kitabın dili EPUB'daki `dc:language` bilgisinden alınır; bilgi yoksa Türkçe varsayılır. Kurulu motorlarda o dil yoksa okuyucu bunu söyler ve Ayarlar'a yönlendirir.

### Kullanım

- Okuyucunun altındaki ▶ düğmesi, ekranda görünen ilk cümleden okumaya başlar. İlk basışta motor başlatılır ve gerekirse model/ses indirilir; aşamalar oynatıcıda ve Ayarlar'da görünür.
- Okunan cümle vurgulanır ve gerekirse sayfa kendiliğinden kayar. Okuma sürerken bir cümleye tıklamak oradan devam ettirir.
- ⏮ / ⏭ önceki ve sonraki cümleye geçer, ■ okumayı bitirir; okuma sırasında `Boşluk` tuşu duraklatır/sürdürür.
- Hız düğmesi 0,8× ile 2× arasında döner; yeni hız bir sonraki cümleden itibaren uygulanır.
- Bölüm bitince bir sonraki bölüme geçilir ve okuma sürer.
- **Kaydet**, açık bölümü WAV dosyası olarak kaydeder (EMA 48 kHz, Piper sesin kendi örnekleme hızında); ilerleme düğmede görünür.
- Bir motor çökerse okuma durur ve hata ayrıntısı gösterilir; motorun günlüğü `ses-motorlari\<motor>\worker.log` dosyasındadır. Okumayı yeniden başlatmak motoru da yeniden başlatır.

### Yeni ses motoru eklemek (geliştiriciler için)

Her motor `engines/<ad>/` altında bir `worker.py` (bkz. `engines/protocol.py`: `load` ve `say` komutlarını uygulayan bir `Engine` sınıfı) ve onu tanımlayan bir `motor.json`'dan (kimlik, ad, sürüm, `protocol: 1`, Python modülü, gerekli paketler, sesler ve dilleri) oluşur. `BUILD_TTS.cmd` her motoru `dist-engines\cevirgec-ses-<id>-win64.zip` olarak paketler; indirme adresleri `engines/catalog.json` dosyasındadır.

## Özel terim sözlüğü

Her kitap için İngilizce terim ve zorunlu Türkçe karşılığı eşleştirilebilir:

| İngilizce terim | Zorunlu karşılık |
|---|---|
| `Murderbot` | `Katilbot` |
| `SecUnit` | `GüvBirim` |

Sözlük isteğe bağlıdır ve `glossary.json` içinde proje bazında saklanır. Kaynağı mevcut çeviri parçasında geçen terimler denetlenir. Karşılık bulunmazsa modelden yalnızca terminolojiyi düzelten ikinci bir yanıt istenir.

Düzeltme yine başarısızsa varsayılan davranış ayrıntıyı `glossary_fixes.log` dosyasına yazıp daha az eksik sonuçla devam etmektir. **Katı sözlük** açıksa bölüm durur ve geçersiz sonuç kaydedilmez. Türkçe çekimli biçimler belirli ek ve ses değişimi kurallarıyla kabul edilir.

**Terim sözlüğü** ile terim eklenir, kaldırılır veya karşılığı değiştirilirse uygulama bu terimin geçtiği tamamlanmış bölümleri bulur ve yeniden çevirmeyi önerir. Henüz çevrilmemiş bölümler zaten güncel sözlüğü kullanır.

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

Emin değilseniz varsayılan değerleri koruyun. **Varsayılanlar** güvenli başlangıç değerlerini geri yükler; değişiklikler **Kaydet** ile uygulanır. Çeviri sürerken LM Studio ve model ayarları kilitlidir. Koyu/açık tema da bu sekmeden (ya da kenar çubuğundaki düğmeden) seçilir ve `ui.json` dosyasında saklanır.

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
2. Sabitlenmiş pywebview/PyInstaller bağımlılıkları yüklenir.
3. `web` klasöründe arayüz derlenir (`npm ci`, `npm run build`); Node.js gereklidir.
4. `dist\Cevirgec.exe` oluşturulur.
5. `config.json` ve `translation_prompt.txt` EXE'nin yanına kopyalanır.

PyInstaller tek dosyalı EXE ilk açılışta geçici klasöre açıldığı için başlangıç birkaç saniye sürebilir. İmzasız PyInstaller uygulamaları bazı antivirüs ürünlerinde yanlış pozitif oluşturabilir. Linux ve macOS için hazır paketleme komut dosyası sağlanmaz.

## Kaynak dosyaların görevleri

| Dosya | Görevi |
|---|---|
| `desktop.py` | Masaüstü giriş noktası; arayüzü pywebview (WebView2) penceresinde açar |
| `bridge.py` | Arayüzden bağımsız uygulama denetleyicisi; arayüzün çağırdığı işlemler ve olaylar |
| `web/` | Svelte arayüz kaynak kodu (`npm run build` ile `web/dist` oluşur) |
| `epub_meta.py` | EPUB başlık, yazar ve kapak bilgisi |
| `tts.py` | Ses motorları: bulma, dil başına seçim, motor süreçleri, zip/indirme ile kurulum, WAV kaydı |
| `engines/` | Ses motorları: ortak protokol (`protocol.py`), EMA ve Piper işçileri, `motor.json` tanımları, `catalog.json` |
| `build_engines.py`, `BUILD_TTS.cmd` | Ses motoru paketlerini derleme |
| `reader.py` | Okuma sekmesi: kitaplık (`library.json`) ve EPUB bölümlerini güvenli HTML'e dönüştürme |
| `gui.py` | Eski PySide6 arayüzü (geçiş sürecinde korunuyor) |
| `app_core.py` | Ayarlar, LM Studio istemcisi, çeviri/analiz/checkpoint çekirdeği |
| `epub_import.py` | EPUB'u proje ve Markdown bölümlerine dönüştürme |
| `epub_output.py` | Çevrilmiş EPUB'u üretme ve doğrulama |
| `cevir.py` | CLI giriş noktası |
| `translation_prompt.txt` | Modelin çeviri sistem talimatı |
| `config.json` | Kalıcı kullanıcı ayarları |
| `requirements.txt` | Sabitlenmiş çalışma ve derleme bağımlılıkları |
| `tests/test_core.py` | Çekirdek, checkpoint ve EPUB testleri |
| `tests/test_bridge.py` | Arayüz denetleyicisi akış testleri |
| `tests/test_reader.py` | Okuyucu, HTML temizleme ve kitaplık testleri |
| `tests/test_tts.py` | Ses motoru yöneticisi testleri (sahte motor gerçek alt süreç olarak) |
| `requirements-tts.txt` | Kaynak koddan sesli okuma ve motor derleme bağımlılıkları |

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
