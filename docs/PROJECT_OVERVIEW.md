# Model Maestro — Proje Tanıtım Dokümanı

> Bu doküman, Model Maestro'nun ne olduğunu, hangi problemleri çözdüğünü ve sağladığı değeri ürün gözüyle anlatır. Teknik derinlik için `ARCHITECTURE.md` ve `API.md`'ye bakınız.

---

## 1. Proje Nedir?

**Model Maestro, kendi sunucunuzda çalışan birleşik bir LLM (büyük dil modeli) ağ geçididir.**

Tek cümleyle: Farklı kaynaklardan gelen yapay zekâ modellerini (yerel Ollama sunucuları, vLLM, AWS Bedrock, Google üzerinden Gemini/Claude, Cursor AI) **tek bir adres ve tek bir anahtar** arkasında toplar; geliştiricilerin kullandığı araçlar (Cursor, Claude Code, Codex, OpenClaw, Grafana, VS Code eklentileri) bu tek adrese bağlanarak tüm modellere erişir.

Bir benzetmeyle: Model Maestro, model sağlayıcıları ile geliştirici araçları arasında duran **akıllı bir santral**dir. Araçlar hangi modelin nerede çalıştığını bilmek zorunda değildir; santral doğru hattı bulur, hat düşerse yedeğine geçer, kimin ne kadar konuştuğunu kaydeder.

İki ana parçadan oluşur:

- **Gateway (API):** Tüm isteklerin geçtiği, yönlendirme/çeviri/güvenlik katmanı.
- **Admin Paneli (Web):** Modelleri, kullanıcıları, sunucuları ve kuralları görsel olarak yönetmek için Next.js tabanlı yönetim arayüzü.

---

## 2. Hangi Problemleri Çözüyor?

### Problem 1 — Her aracın kendi format ve bağlantı beklentisi var
Cursor OpenAI formatı ister, Claude Code Anthropic formatı ister, Ollama tabanlı araçlar Ollama'nın kendi protokolünü ister, Codex Desktop "Responses API" ister. Her model sağlayıcısına her aracı ayrı ayrı bağlamak pratikte imkânsızdır.

**Çözüm:** Maestro her aracın kendi dilini konuşan "ön kapılar" sunar (OpenAI uyumlu `/v1`, Ollama native `/api`, Anthropic uyumlu `/claude`, Codex, Cursor, OpenClaw, Grafana Assistant). İçeride hepsi aynı boru hattına akar; format çevirisini Maestro yapar. **Bir kez kur, her araçtan bağlan.**

### Problem 2 — Modeller dağınık, isimleri karmaşık, yerleri değişken
Modeller birden fazla makinede, farklı sağlayıcılarda ve teknik isimlerle (`gpt-oss:120b-cloud` gibi) yaşar. Bir model başka sunucuya taşındığında tüm araçların ayarlarını güncellemek gerekir.

**Çözüm:** **Model eşleme (mapping)** katmanı — arkadaki gerçek model ne olursa olsun, kullanıcılar sabit ve anlaşılır bir "görünen isim" kullanır. Arka planda model değişse bile kullanıcı tarafında hiçbir şey değişmez.

### Problem 3 — Tek sunucu / tek model = tek hata noktası
Yerel bir GPU makinesi çökerse ya da bir bulut modeli kota sınırına takılırsa, iş durur.

**Çözüm:** **İki katmanlı otomatik yedekleme (failover):** Önce aynı model başka bir sağlıklı sunucuda denenir; o da yoksa **model grubundaki** sıradaki yedek modele geçilir. Kullanıcı çoğu zaman kesintiyi fark etmez. Ek olarak: yük dengeleme (en az yüklü / öncelik / ağırlık / sıralı), sağlık kontrolleri, modelleri sıcak tutan "warmup" ve çok büyük isteklerde otomatik olarak geniş-bağlamlı modele geçen 413 yedeği.

### Problem 4 — "Kim, neyi, ne kadar kullanıyor?" görünmez
Ekipçe paylaşılan model erişiminde kullanım kontrolü ve maliyet görünürlüğü yoktur.

**Çözüm:** **Kullanıcı bazlı yönetişim:** Her kullanıcıya kendi anahtarı (JWT token) verilir; hangi modellere ve hangi sunuculara erişebileceği tek tek belirlenebilir, günlük istek/token limitleri konabilir. Her istek kaydedilir: hangi kullanıcı, hangi model, hangi araçtan (Cursor/Claude/OpenClaw...), kaç token, ne kadar sürede. Dashboard'da grafiklerle izlenir; yönetici işlemleri ayrıca denetim kaydına (audit log) düşer.

### Problem 5 — Modellerin huysuzlukları istemcilere sızıyor
Bazı modeller araç çağrılarını (tool calls) standart dışı formatta üretir (Kimi, DeepSeek), bazıları belirli parametreleri kabul etmez, bazıları görsel desteklemez, bazılarının bağlam penceresi ayarlanmazsa istekler kırpılır.

**Çözüm:** Maestro bu pürüzleri **ortada törpüler**: standart dışı tool-call çıktılarını OpenAI formatına çevirir, model bazında desteklenmeyen parametreleri ayıklar, görsel desteklemeyen modele giden mesajlardan görselleri temizler, doğru bağlam penceresini (num_ctx) otomatik enjekte eder, "düşünme" (reasoning) içeriğini ayrıştırır. İstemciler her modeli "standart bir modelmiş gibi" kullanır.

### Problem 6 — Kurum politikalarını her kullanıcıya tek tek anlatamazsınız
"Bu modelde şu kurallara uy", "bu kullanıcının tüm istekleri şu dilde yanıtlansın" gibi politikaları istemci tarafında uygulatmak güvenilir değildir.

**Çözüm:** **Sistem promptu enjeksiyonu** — yönetici; kullanıcı, model, eşleme, grup veya sunucu bazında sistem promptları tanımlar. Eşleşen her istek bu promptları otomatik ve **kullanıcıya görünmeden** alır. Birden fazla kural eşleşirse hiyerarşik olarak katmanlanır (kullanıcı > sunucu > grup > model > eşleme) ve sürükle-bırak ile sıralanabilir.

### Problem 7 — Kendi makinenizdeki gateway'e dışarıdan erişim zor
Cursor gibi bazı araçların sunucuları, gateway'inize internetten erişebilmelidir; evdeki/ofisteki makine ise genelde dışarıya kapalıdır.

**Çözüm:** Panele gömülü **tek tıkla public tünel** (Cloudflare veya ngrok). Ayrı reverse proxy kurmadan gateway güvenli şekilde internete açılır.

---

## 3. Değer Önerileri

| Değer | Açıklama |
|---|---|
| **Tek kapı, tüm modeller** | Beş farklı sağlayıcı tipindeki (Ollama, vLLM, Bedrock, Antigravity, Cursor) tüm modeller tek URL + tek token ile kullanılır. |
| **Araç bağımsızlığı** | Cursor, Claude Code/Desktop, Codex (CLI + Desktop), OpenClaw, Grafana Assistant, VS Code eklentileri — hepsi kendi doğal formatıyla bağlanır; hazır kurulum rehberi paneldedir. |
| **Kesintisizlik** | Sunucu/model arızasında otomatik yedeğe geçiş; canlı stream'i bozmamaya dikkat eden arka plan görevleri; modelleri sıcak tutan warmup. |
| **Kontrol ve görünürlük** | Kullanıcı bazlı erişim ve limitler, kaynak bazlı istek kayıtları, kullanım grafikleri, denetim izi. Kim-ne-nerede sorusunun cevabı her an hazır. |
| **Şeffaf politika uygulama** | Sistem promptu enjeksiyonu ile davranış kuralları merkezi olarak, kullanıcı deneyimini bozmadan uygulanır. |
| **Esnek model sunumu** | Görünen isimler, model grupları ve stratejileri sayesinde "tek model gibi görünen ama arkada değişebilen" mantıksal modeller sunulur; IDE'de model değiştirmeden arkadaki motor değiştirilebilir. |
| **Kolay işletme** | Docker Compose ile kurulum, açılışta otomatik veritabanı migration'ı, Makefile ile tek komutluk operasyonlar, panelden tünel yönetimi. Tek yöneticili küçük/orta ekipler için tasarlanmıştır. |
| **Veri sizde** | Tamamen self-hosted; istekler sizin altyapınızdan geçer, kayıtlar sizin veritabanınızda durur. |

---

## 4. Nasıl Çalışır? (Yüksek Seviye)

```
Geliştirici Aracı (Cursor / Claude Code / Codex / OpenClaw / Grafana ...)
        │  kendi doğal formatında istek + kullanıcı token'ı
        ▼
┌───────────────────────── MODEL MAESTRO ─────────────────────────┐
│ 1. Kimlik doğrulama (JWT) + erişim ve limit kontrolü            │
│ 2. Model çözümleme: grup → üye seçimi, görünen isim → gerçek ad │
│ 3. Sistem promptu enjeksiyonu (kullanıcı/sunucu/grup/model)     │
│ 4. Sunucu seçimi: yük dengeleme + sağlık durumu                 │
│ 5. İstek düzeltmeleri: parametre ayıklama, num_ctx, görseller   │
│ 6. Gönderim + stream; hata olursa → başka sunucu → yedek model  │
│ 7. Yanıt çevirisi (tool-call normalizasyonu, format dönüşümü)   │
│ 8. Kullanım kaydı (arka planda, isteği yavaşlatmadan)           │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
Model Sağlayıcıları: Ollama sunucuları · vLLM · AWS Bedrock · Antigravity (Gemini/Claude) · Cursor AI
```

Performans için sık kullanılan her şey (eşlemeler, kullanıcı erişimleri, limitler, aktif promptlar, sunucu yükleri) Redis'te önbelleklenir; kayıtlar toplu yazılır. Kalıcı veri PostgreSQL'dedir.

---

## 5. Yetenek Haritası

### 5.1 İstemci Entegrasyonları (kim bağlanabilir?)
- **OpenAI uyumlu her şey** (`/v1/*`): SDK'lar, kütüphaneler, çoğu araç.
- **Ollama native araçlar** (`/api/*`): Ollama protokolü konuşan tüm ekosistem.
- **Claude Code & Claude Desktop (Cowork)** (`/claude/*`): Anthropic Messages API birebir; araç tanımları ve stream olayları dahil çift yönlü çeviri.
- **OpenAI Codex — CLI ve Desktop** (`/codex/*`): Responses API dahil; Desktop için zengin model kataloğu üretir. `codex-maestro` başlatıcı script'i ile tek komutla açılır.
- **Cursor IDE** (`/cursor/*`): Cursor'un karma formatını karşılar (public erişim/tünel gerektirir).
- **OpenClaw** (`/openclaw/*`): Ollama sağlayıcısı olarak eklenir.
- **Grafana Assistant** (`/grafana/*`): Grafana'nın resmî asistan eklentisinin arka ucu olarak çalışır; hangi modelin kullanılacağı panelden seçilir.
- **Web araması** (`/res/v1/web/search`): Brave Search API taklidi — arama eklentileri, arkada Ollama web aramasıyla çalışır.

### 5.2 Model Yönetimi
- Sağlayıcı bazında model keşfi ve senkronizasyonu (otomatik arka plan taraması).
- Model çekme (pull), silme, kullanılabilirlik aç/kapat (global veya sunucu bazında).
- Görünen isim eşlemeleri + yetenek etiketleri (tools/vision/thinking) + bağlam uzunluğu.
- Model grupları: round-robin / ağırlıklı / öncelikli stratejiler, yedek zincirleri, katalogda tek isim olarak görünme, sürükle-bırak sıralama.
- Model bazlı konfigürasyon kuralları (prefix ile hedefleme): desteklenmeyen parametreler, izinli araç setleri, bakım modu.

### 5.3 Güvenilirlik ve Performans
- Çok sunuculu yük dengeleme (en az yüklü / öncelik / ağırlık / sıralı).
- Sunucu sağlık kontrolleri (sağlayıcı tipine özel) + otomatik dışarıda bırakma.
- İki katmanlı failover + 413 (istek çok büyük) yedeği.
- Model warmup (soğuk başlatma gecikmesini önler) — canlı stream varken erteleme.
- WAF korumalı uzak sunucular için otomatik çerez yenileme.
- `node:kod:model` ön ekiyle isteği belirli bir sunucuya sabitleme.

### 5.4 Erişim, Güvenlik ve Yönetişim
- Kullanıcı başına JWT token; pasifleştirme / token yenileme.
- Model, sunucu ve sunucu-model düzeyinde erişim izinleri.
- Günlük istek ve token limitleri.
- Kaynak etiketli aktivite kayıtları (hangi araçtan geldiği dahil) + denetim izi.
- Kullanıcı/sunucu/grup/model/eşleme bazlı şeffaf sistem promptu enjeksiyonu.

### 5.5 İzleme ve Panel
- Dashboard: sistem durumu, istek ve token grafikleri, en çok kullanılan modeller, tarih filtreli "Top Users" ve kullanıcı başına model dağılımı.
- İstek logları (filtrelenebilir), denetim logları.
- Sunucu başına yük ve model dağılımı görünümleri.
- Panelden: kullanıcılar, sunucular, modeller, eşlemeler, gruplar, sistem promptları, araç setleri, Grafana modeli, tünel ve genel ayarlar.
- **Guide sayfası:** Son kullanıcının aracını bağlaması için kopyala-yapıştır kurulum rehberleri.

---

## 6. Kimler İçin? Tipik Senaryolar

**Hedef profil:** Kendi altyapısında LLM çalıştıran ve/veya birden çok sağlayıcıyı bir arada kullanan, tek yöneticili küçük-orta ölçekli geliştirici ekipleri veya ileri düzey bireysel kullanıcılar.

- **Ekip içi ortak AI altyapısı:** Ekipteki herkese kendi token'ı verilir; kim hangi modele erişir, günde ne kadar kullanır — merkezi kontrol. Evdeki GPU'lar + bulut modelleri tek havuzda.
- **IDE'lerde "kendi modelini getir":** Cursor/Claude Code/Codex'i şirketin veya kişinin kendi modellerine bağlamak; abonelik modellerinin yerine ya da yanında self-hosted modeller kullanmak.
- **Model deneme ve kademeli geçiş:** Grup içinde eski/yeni modeli değiştirip istemcilere hiç dokunmadan A/B geçişi yapmak.
- **Politika zorunlu ortamlar:** Belirli modellere/kullanıcılara merkezi davranış kuralları (dil, üslup, güvenlik talimatları) enjekte etmek.
- **Grafana'ya self-hosted asistan:** Grafana Assistant'ı bulut yerine kendi modellerinizle çalıştırmak.

---

## 7. Teknoloji ve Dağıtım (Kısa)

| Katman | Teknoloji |
|---|---|
| Gateway | Python 3.11 · FastAPI · Uvicorn (streaming ayarlı) |
| Veritabanı | PostgreSQL (async SQLAlchemy + Alembic migration; açılışta otomatik) |
| Önbellek / kuyruk | Redis (erişim, eşleme, limit, prompt önbellekleri + log kuyruğu) |
| Panel | Next.js 16 · React 19 · Tailwind v4 · shadcn/Radix · Recharts · React Query |
| Dağıtım | Docker Compose (dev: tam yerel yığın, prod: API + panel) · Makefile hedefleri |
| Dışa açılım | Gömülü Cloudflare/ngrok tüneli (panelden yönetilir) |

Kurulum özeti: `make setup` (dev ortam + veritabanı) → panelden sunucu/model tanımla → kullanıcı oluştur → Guide sayfasındaki adresle aracını bağla.

---

## 8. Özet

Model Maestro, **"modellerim her yerde, araçlarım hepsiyle konuşamıyor, kimin ne kullandığını göremiyorum"** probleminin tek parça cevabıdır. Sağlayıcıları tek kapıda toplar, araç formatlarını çevirir, arızada yedeğe geçer, erişimi ve tüketimi kullanıcı bazında yönetir, kurum politikalarını görünmez şekilde uygular ve tüm bunları tek bir web panelinden yönetilebilir kılar — verileriniz kendi altyapınızda kalarak.
