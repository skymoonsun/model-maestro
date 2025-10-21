# Ollama Proxy API

FastAPI tabanlı JWT authentication ve cloud model mapping özellikli Ollama proxy servisi.

## Özellikler

- 🔐 **JWT Authentication**: Token tabanlı güvenli erişim
- 🔄 **Model Mapping**: Cloud modellerin isimlerini otomatik manipüle eder
- 🐳 **Docker Support**: Kolay deployment için Docker ve Docker Compose
- 🛠️ **CLI Tool**: Kullanıcı yönetimi için komut satırı aracı
- 📡 **Full Ollama API**: Tüm Ollama endpoint'lerini destekler

## Kurulum

### 1. Projeyi Klonlayın

```bash
git clone <repository-url>
cd ollama-proxy-api
```

### 2. Environment Dosyasını Oluşturun

```bash
cp .env.example .env
```

`.env` dosyasını düzenleyin:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
JWT_SECRET_KEY=super-secret-key-change-this-immediately
LOG_LEVEL=INFO
```

### 3. Model Mapping'i Yapılandırın

`config/model_mappings.json` dosyasını ihtiyacınıza göre düzenleyin:

```json
{
  "mappings": {
    "gpt-oss:120b": "gpt-oss:120b-cloud",
    "gpt-oss:20b": "gpt-oss:20b-cloud",
    "deepseek-v3.1:671b": "deepseek-v3.1:671b-cloud",
    "kimi-k2:1t": "kimi-k2:1t-cloud",
    "qwen3-coder:480b": "qwen3-coder:480b-cloud",
    "glm-4.6": "glm-4.6:cloud"
  }
}
```

### 4. Docker ile Çalıştırın

```bash
docker-compose up -d
```

Servis `http://localhost:8000` adresinde çalışacaktır.

## Kullanıcı Yönetimi

### Kullanıcı Oluşturma

```bash
# Yöntem 1 (Önerilen)
docker exec ollama-proxy create-user john

# Yöntem 2
docker exec ollama-proxy python cli.py create-user john
```

Çıktı:
```
✓ User created successfully!

Username: john
Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Created: 2024-01-20T10:30:00.000000

⚠ Save this token securely. You can refresh it later if needed.
```

### Kullanıcıları Listeleme

```bash
docker exec ollama-proxy list-users
```

### Kullanıcı Bilgilerini Görüntüleme

```bash
docker exec ollama-proxy show-user john
```

### Token Yenileme

```bash
docker exec ollama-proxy refresh-token john
```

### Kullanıcı Silme

```bash
docker exec ollama-proxy delete-user john
```

## API Kullanımı

### Authentication

Tüm API isteklerinde `Authorization` header'ı gereklidir:

```bash
Authorization: Bearer <your-jwt-token>
```

### Chat Completion

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [
      {"role": "user", "content": "Merhaba, nasılsın?"}
    ]
  }'
```

**Not**: İstek `gpt-oss:120b` ile gönderilir, ama arka planda Ollama'ya `gpt-oss:120b-cloud` olarak iletilir.

### Text Generation

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v3.1:671b",
    "prompt": "Bir Python kodu yaz"
  }'
```

### Embeddings

```bash
curl -X POST http://localhost:8000/api/embeddings \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3:latest",
    "prompt": "Merhaba dünya"
  }'
```

### Model Listesi

```bash
curl -X GET http://localhost:8000/api/tags \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Dönen modeller otomatik olarak `-cloud` suffix'i olmadan görüntülenir.

### Streaming

Streaming istekler için `stream: true` parametresi ekleyin:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [{"role": "user", "content": "Uzun bir hikaye anlat"}],
    "stream": true
  }'
```

## Desteklenen Endpoint'ler

- `POST /api/generate` - Text generation
- `POST /api/chat` - Chat completion
- `POST /api/embeddings` - Generate embeddings
- `GET /api/tags` - List models
- `POST /api/show` - Show model info
- `POST /api/copy` - Copy model
- `DELETE /api/delete` - Delete model
- `POST /api/pull` - Pull model
- `POST /api/push` - Push model
- `POST /api/create` - Create model from Modelfile

### Model Mapping Mantığı

**Client → Proxy → Ollama:**
- Client `gpt-oss:120b` gönderir
- Proxy mapping'i kontrol eder
- Ollama'ya `gpt-oss:120b-cloud` olarak iletir

**Ollama → Proxy → Client:**
- Ollama `gpt-oss:120b-cloud` döner
- Proxy reverse mapping yapar
- Client'a `gpt-oss:120b` döner

**Model Listesi (/api/tags):**
- Ollama'dan tüm modelleri al
- Cloud modellerin `-cloud` suffix'ini kaldır
- Local modelleri olduğu gibi bırak
- Cloud modellerden `remote_host` alanını kaldır (tüm modeller local gibi görünür)

## Geliştirme

### Local Olarak Çalıştırma

```bash
# Virtual environment oluştur
python -m venv venv
source venv/bin/activate  # Linux/Mac
# veya
venv\Scripts\activate  # Windows

# Bağımlılıkları yükle
pip install -r requirements.txt

# Çalıştır
uvicorn app.main:app --reload
```

### CLI'yi Local Çalıştırma

```bash
python cli.py create-user john
python cli.py list-users
```

## Güvenlik

- JWT secret key'i mutlaka değiştirin (`.env` dosyasında)
- Token'ları güvenli bir şekilde saklayın
- HTTPS kullanın (production için)
- Docker container'ı güvenlik güncellemeleri için düzenli olarak yeniden build edin

## Sorun Giderme

### Ollama'ya bağlanamıyor

`.env` dosyasındaki `OLLAMA_BASE_URL` adresini kontrol edin:

- Docker içinde (macOS/Windows): `http://host.docker.internal:11434`
- Docker içinde (Linux): `http://localhost:11434` (ve `docker-compose.yml`'de `network_mode: "host"` kullanın)
- Local: `http://localhost:11434`
- Uzak sunucu: `http://sunucu-ip:11434`

**Linux'ta Docker Network Sorunu:**

Linux sistemlerde container'dan host'a erişim için `docker-compose.yml` dosyasını şu şekilde güncelleyin:

```yaml
services:
  ollama-proxy:
    build: .
    container_name: ollama-proxy
    network_mode: "host"  # Bu satırı ekleyin
    volumes:
      - ./data:/app/data
      - ./config:/app/config
    env_file:
      - .env
    restart: unless-stopped
```

Ve `.env` dosyasında:
```env
OLLAMA_BASE_URL=http://localhost:11434
```

### 401 Unauthorized hatası

- Token'ın doğru gönderildiğinden emin olun
- `Authorization: Bearer <token>` formatında olmalı
- Token'ın geçerli olduğundan emin olun
- Kullanıcının varolduğundan emin olun

### Model bulunamıyor

- Model isminin mapping'de doğru tanımlandığından emin olun
- Ollama'da modelin kurulu olduğunu kontrol edin: `ollama list`

## Lisans

MIT

## Katkıda Bulunma

Pull request'ler kabul edilir. Büyük değişiklikler için önce bir issue açın.

