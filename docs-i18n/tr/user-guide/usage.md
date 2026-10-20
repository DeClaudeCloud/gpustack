---
translation_status: translated
---

# Kullanım { #usage }

**Kullanım** sayfası, GPUStack kaynaklarınızın zaman içinde nasıl tüketildiğini (LLM token'lari, GPU/CPU örneği çalışma zamanı ve depolama kapasitesi) bildirir; böylece benimsemeyi izleyebilir, tüketimi kullanıcılara atfedebilir ve maliyetle ilgili nedenleri öğrenebilirsiniz.

## Kullanım Sayfasına Erişim { #accessing-the-usage-page }

1. Sol gezinme menüsünden (çubuk grafik simgesi) `Usage` sayfasına gidin.
2. Bir sekme seçin — `Summary`, `Tokens`, `GPU Instances`, `Storage` veya `Resource Events`.
3. Raporun kapsamını belirlemek için `date range`'yi (ve diğer filtreleri) ayarlayın.

Sekmeler şunlardır:

- **Özet** — token'lara, hesaplamaya ve depolamaya tek bakışta genel bakış.
- **Tokens** — Modele/kullanıcıya/API anahtarına göre ayrılmış LLM token tüketimi.
- **GPU Örnekleri** — Örnek türüne / örneğine / kullanıcıya göre ayrılmış GPU/CPU örneği çalışma zamanı.
- **Depolama** — depolama kapasitesi kullanımı, depolamaya/kullanıcıya göre ayrılmış.
- **Kaynak Olayları** — işlem/depolama numaralarının arkasındaki yaşam döngüsü denetim günlüğü.

Tek tek çıkarım istekleri, gezinmede `Usage` öğesinin yanındaki ayrı [Request Logs](#request-logs) sayfasında listelenir.

## Ne ölçülür { #what-gets-metered }

| Kaynak | Ölçülüyken… | Kova ayrıntı düzeyi |
| ----------------- | ------------------------------------------------------------------------------------------------ | ------------------ |
| Tokenlar | Ağ geçidi üzerinden bir çıkarım isteği sunulur.                                              | Günlük |
| GPU/CPU örnekleri | Örnek, çalışma (ölçülü) aşamasındadır. **Stopped** veya silinmiş örnek oluşmaz. | Saatlik |
| Depolama | Oluşturma işleminden silme işlemine kadar kapasite, takılı olup olmadığına bakılmaksızın ölçülür.            | Saatlik |

## Görünürlük { #visibility }

GPUStack'in iki rolü vardır — **Admin** ve **User** (bkz. [Kullanıcı Yönetimi](user-management.md)):

- **Yönetici** — tüm kullanıcılar arasındaki kullanımı görüntüleyebilir ve **Kullanıcıya göre filtrele** kontrolüyle görünümü daraltabilir.
- **Kullanıcı** — yalnızca kendi kullanımlarını görüntüleyebilir; Kullanıcı bazında dökümler mevcut değildir.

## Ortak kontroller { #common-controls }

Her sekme (kendi filtreleri olan Kaynak Etkinlikleri hariç) aynı kontrolleri paylaşır:

- **Tarih aralığı** — raporlama penceresi. Varsayılan olarak son 30 gün kullanılır.
- **Kullanıcıya göre filtrele** — belirli kullanıcılarla sınırlandırın (yalnızca yöneticiler). Belirteçler sekmesi ayrıca **Modele göre filtrele** ve **API anahtarına göre filtrele** seçeneklerini sunar.
- **Yenile** — mevcut filtrelerle yeniden getir.
- **Metrik** — eğilim grafiğinde ve temel gösterge sıralamasında kullanılacak değer (ör. GPU Saatleri veya Örnek Saatleri).
- **Group by** — trend grafiğini grup başına bir seriye bölün (ör. örnek türü başına).
- **Ayrıntı düzeyi** — eğilimin zaman aralığı. GPU Örnekleri ve Depolama **Saat / Gün / Hafta / Ay**, Token'lar ise yalnızca **Gün / Hafta / Ay** düzeylerini destekler; token kullanımı günlük toplandığından saatlik aralığı yoktur.
- **Dışa Aktar** (indirme simgesi, Belirteçler / GPU Örnekleri / Depolama'da mevcuttur) — mevcut filtrelenmiş dökümün bir önizlemesini açar ve onu indirmenize olanak tanır.

## Metrik tanımlar { #metric-definitions }

| Metrik | Anlamı |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Giriş Token'ları** | İstem token'larıdır. **Önbellekten Karşılanan Giriş Token'ları**, istem önbelleğinden sunulan alt kümedir. |
| **Çıkış Token'ları** | Üretilen yanıt token'larıdır. |
| **Toplam Token** | Giriş + çıkış token'ları. |
| **Örnek Saatleri** | Örneğin gerçek geçen çalışma süresi: Σ(çalışma süresi); örnekteki kart sayısından bağımsızdır. |
| **GPU Saatleri** | Hızlandırıcı çalışma süresi: Σ(çalışma süresi × GPU kartı sayısı). 2 GPU'lu bir örneğin 1 saat çalışması = 2 GPU Saati, ancak 1 Örnek Saati. |
| **GB-Gün** | Zaman içinde ayrılan depolama: kapasite (GB) × tutulduğu gün sayısı. |
| **GB-Saat** | GB-Gün ile aynı ölçümün saat cinsinden ifadesi. |
| **Etkin Örnekler** / **Etkin Depolamalar** / **Etkin Kullanıcılar** | Pencere içinde hala yayında olan kaynaklar (**Silinmiş** değil).                                                  |
| **Son Etkinlik** | Kaynağın kullanım biriktirdiği en son zaman aralığı. |

!!! note

    Silinen kaynaklar geçmiş kullanımlarını korur; silinmiş bir model, örnek veya depolama tablolarda görünmeye devam eder (**Deleted** olarak işaretlenir) ve toplamlara katkıda bulunur, ancak artık **Etkin** olarak sayılmaz.

## Özet { #summary }

![Kullanım Özeti](../assets/usage/usage-summary.png)

Özet sekmesi **Token'lar**, **İşlem** ve **Depolama** olmak üzere üç bölüm içerir. Her bölümde temel istatistikler, türe göre dağılımı gösteren bir halka grafik ve seçilen aralık için eğilim grafiği bulunur. Böylece sekme değiştirmeden üç kaynak türünü de görebilirsiniz.

## Token'lar { #tokens }

![Kullanım Token'ları](../assets/usage/usage-tokens.png)

LLM çıkarımındaki token tüketimi. Üstteki temel göstergeler: **Giriş / Çıkış / Toplam Token**, **API İstekleri** ve **Kullanılan Modeller**.

Alt kısım detay tablosunu üç şekilde gruplandırır:

- **Models** — model başına: Giriş Belirteçleri / Önbelleğe Alınmış Giriş Belirteçleri / Çıkış Belirteçleri / Toplam Belirteçler, API İstekleri, Son Etkin.
- **Kullanıcılar** — kullanıcı başına (yalnızca yöneticiler).
- **API Anahtarları** — API anahtarı başına.

## GPU Örnekleri { #gpu-instances }

![GPU Bulut Sunucularının Kullanımı](../assets/usage/usage-gpu-instances.png)

GPU ve CPU örneği çalışma zamanı. KPI'lar: **GPU Saatleri**, **Örnek Saatleri**, **Etkin Örnekler**, **Aktif Kullanıcılar**. Grafik metriği olarak **GPU Saatleri** veya **Örnek Saatleri**'yi seçin ve eğilimi **örnek türü**, **örnek** veya **kullanıcı**'ye göre gruplayın.

Ayrıntı tablosu şu şekilde gruplandırılır:

- **Örnek Türleri** — örnek türü başına: GPU Saatleri, Örnek Saatleri, Etkin Örnekler, Son Etkin.
- **Örnekler** — bireysel örnek başına.
- **Kullanıcılar** — kullanıcı başına (yalnızca yöneticiler).

!!! note

    CPU örneklerinin hızlandırıcısı yoktur, dolayısıyla **Örnek Saatleri**'ye katkıda bulunurlar ancak **GPU Saatleri**'e katkıda bulunmazlar.

## Depolama { #storage }

![Kullanım Depolama](../assets/usage/usage-storage.png)

Depolama kapasitesi kullanımı. KPI'lar: **GB-Gün**, **GB-Saat**, **Aktif Depolamalar**, **Aktif Kullanıcılar**. Grafik metriği olarak **GB-Gün** veya **GB-Saat**'i seçin ve **storage** veya **user**'e göre gruplayın.

Ayrıntı tablosu şu şekilde gruplandırılır:

- **Depolama** — birim başına: Tür, Kapasite, GB-Gün, GB-Saat, Son Etkin.
- **Kullanıcılar** — kullanıcı başına (yalnızca yöneticiler).

!!! note

    Depolama, eklemeden bağımsız olarak oluşturma aşamasından silme işlemine kadar ölçüldüğü için, mevcut ancak boşta olan depolama alanı yine de GB-Gün/GB-Saat olarak hesaplanır.

## Kaynak Etkinlikleri { #resource-events }

![Kaynak Kullanımı Olayları](../assets/usage/usage-resource-events.png)

İşlem ve depolama ölçümlerini açıklayan yaşam döngüsü denetim günlüğüdür; ölçümle ilgili her durum geçişi için bir satır bulunur. Bir değerin neden öyle olduğunu anlamaya yardımcı olur.

Filtreler: **tarih aralığı**, **kaynak türü** (GPU Örneği / Depolama), **olay türü** ve bulanık **kaynak adı** arama.

## İstek Günlükleri { #request-logs }

![İstek Günlükleri](../assets/usage/usage-request-logs.png)

Her çıkarım isteği için bir satır, en yenisi en üstte — Tokens sayılarının arkasındaki istek bazlı görünüm. Gezinmedeki `Request Logs` öğesinden açın.

- **İstatistikler** — toplam istek, başarı oranı, kullanıcı başarısı, ortalama ve p95 gecikme, ilk tokendan sonra saniyedeki token sayısı olarak çıktı hızı ve ortalama ilk token süresi, ayrıca toplam girdi ve çıktı tokenları. Her biri, seçilen dönemden hemen önceki eşit uzunluktaki dönemle karşılaştırılır.
- **İstek hacmi** — zaman içinde başarılı, başarısız ve tamamlanmamış istekler.
- **Tablo** — zaman, tür, durum, istek kimliği, model, süre, ilk token süresi, çıktı hızı, tokenlar, API anahtarı, kullanıcı aracısı ve (Yöneticiler için) kullanıcı. İsteğin tüm ayrıntılarını görmek için bir satırı veya istek kimliğini seçin; buna ağ geçidinin çağırana `X-GPUStack-Request-Id` yanıt başlığında döndürdüğü ağ geçidi istek kimliği de dahildir.

Filtreler: **zaman aralığı** (son 15 dakikadan son 30 güne kadar veya özel aralık), istek kimliği, yanıt kimliği veya modele göre **arama**, **durum**, **model**, **API anahtarı**, **kullanıcı** (yalnızca Yöneticiler) ve **akış**. Yeni istekleri tamamlandıkça izlemek için **Live** özelliğini açın; göreli zaman aralıklarında kullanılabilir.

Bir isteğin durumu şunlardan biridir:

| Durum      | Anlamı                                                                                                                                    |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Success    | Model bir yanıt döndürdü.                                                                                                                 |
| Error      | İstek başarısız oldu veya yukarı akış bir model yanıtı döndürmedi.                                                                        |
| Incomplete | Yanıt, token kullanımı bildirilmeden önce sona erdi: istemci bağlantıyı kesti veya yukarı akış yanıtın ortasında durdu. Token sayıları tahminidir. |

**Kullanıcı başarısı**, yedek bir hedefte yeniden denenen isteği bir kez sayar ve denemelerden biri başarılıysa başarılı kabul eder; **başarı oranı** her denemeyi sayar.

!!! note

    İstekler tamamlandıktan birkaç saniye sonra görünür. Ağ geçidinin bir modele ulaşmadan önce reddettiği istekler (geçersiz bir API anahtarı veya bilinmeyen bir model adı) listelenmez. Bu sayfadaki saatler tarayıcınızın saat diliminde gösterilir.

    Ağ geçidi üzerinden sunulan bir isteğin kullanıcı aracısı, gömülü ağ geçidinin erişim günlüğünden okunur; bu nedenle GPUStack ağ geçidini gömülü olarak çalıştırdığında (kapsayıcı imajının varsayılanı) gösterilir, küme içi veya harici bir ağ geçidiyle boş kalır.

## Saat Dilimi { #timezone }

Kullanım sayfasındaki tüm tarihler ve saatler (eğilim grupları, **Son Aktif** ve kaynak olayı saatleri) tek bir yapılandırılabilir **toplama zaman diliminde** gösterilir, böylece her sekme aynı takvim sınırları üzerinde sıralanır. `GPUSTACK_TIMEZONE` sunucu ortamı değişkeni tarafından kontrol edilir (eski `GPUSTACK_USAGE_ROLLUP_TIMEZONE` hala kullanımdan kaldırılmış bir takma ad olarak çalışır) ve varsayılan olarak sunucunun işletim sistemi yerel saat dilimini kullanır. Yaz Saati Uygulaması notu da dahil olmak üzere ayrıntılar için [Ortam Değişkenleri](../environment-variables.md#usage-tracking-configuration)'ye bakın.

## Veri saklama { #data-retention }

Token, bilgi işlem ve depolama toplamaları yaklaşık 13 ay boyunca saklanır ve sonrasında bir arka plan işi ile arşiv tablolarına taşınırlar. Saklama pencereleri ve arşiv programları `GPUSTACK_*_RETENTION_MONTHS` ve `GPUSTACK_*_ARCHIVE_CRON` ortam değişkenleri aracılığıyla yapılandırılabilir.
