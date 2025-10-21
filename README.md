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

### 2. PostgreSQL Kurulumu

Bu proje kullanıcı bilgileri ve model mapping'leri için PostgreSQL kullanır.

#### Yerel PostgreSQL Kurulumu

**macOS:**
```bash
brew install postgresql@15
brew services start postgresql@15
createdb ollama_proxy
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo -u postgres createdb ollama_proxy
sudo -u postgres createuser -P ollama_user
```

**Docker ile PostgreSQL (Opsiyonel):**
```bash
docker run -d \
  --name ollama-postgres \
  -e POSTGRES_DB=ollama_proxy \
  -e POSTGRES_USER=ollama_user \
  -e POSTGRES_PASSWORD=changeme \
  -p 5432:5432 \
  postgres:15-alpine
```

### 3. Environment Dosyasını Oluşturun

```bash
cp .env.example .env
```

`.env` dosyasını düzenleyin:

```env
# Ollama Configuration
OLLAMA_BASE_URL=http://host.docker.internal:11434
JWT_SECRET_KEY=super-secret-key-change-this-immediately
LOG_LEVEL=INFO

# PostgreSQL Configuration
DATABASE_URL=postgresql+asyncpg://ollama_user:changeme@localhost:5432/ollama_proxy

# Admin Token (admin endpoint'leri için)
ADMIN_TOKEN=admin-super-secret-token-change-this-in-production
```

### 4. Model Mapping'i Yapılandırın

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

### 5. Database Migration'ları Çalıştırın

Docker container'ı başlatmadan önce veya başlattıktan sonra migration'ları çalıştırın:

```bash
# Container içinde migration çalıştırma
docker-compose up -d
docker exec ollama-proxy alembic upgrade head
```

### 6. Docker ile Çalıştırın

```bash
docker-compose up -d
```

Servis `http://localhost:8000` adresinde çalışacaktır.

### 7. İlk Model Mapping'leri Oluşturun (Opsiyonel)

Admin API kullanarak model mapping'leri oluşturabilirsiniz:

```bash
curl -X POST http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "gpt-oss:120b",
    "real_name": "gpt-oss:120b-cloud"
  }'
```

Veya JSON dosyasından import etmek için migration script yazabilirsiniz.

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

## Admin API

Admin endpoint'leri ile kullanıcı ve model yönetimi yapabilirsiniz. Tüm admin endpoint'leri `ADMIN_TOKEN` gerektirir.

### Authentication

Admin endpoint'leri için `.env` dosyasında tanımlı `ADMIN_TOKEN` kullanılır:

```bash
Authorization: Bearer YOUR_ADMIN_TOKEN
```

### Kullanıcı Yönetimi Endpoint'leri

#### Kullanıcı Oluşturma

```bash
curl -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "john"}'
```

**Response:**
```json
{
  "username": "john",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "created_at": "2024-01-20T10:30:00.000000",
  "updated_at": null,
  "is_active": true
}
```

#### Kullanıcı Listesini Görüntüleme

```bash
curl -X GET http://localhost:8000/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
[
  {
    "username": "john",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "created_at": "2024-01-20T10:30:00.000000",
    "updated_at": null,
    "is_active": true,
    "has_all_models": false,
    "models": ["gpt-oss:120b", "deepseek-v3.1:671b"]
  }
]
```

#### Kullanıcı Bilgilerini Görüntüleme

```bash
curl -X GET http://localhost:8000/admin/users/john \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

#### Token Yenileme

```bash
curl -X PUT http://localhost:8000/admin/users/john/token \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
{
  "username": "john",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "created_at": "2024-01-20T10:30:00.000000",
  "updated_at": "2024-01-20T11:00:00.000000",
  "is_active": true
}
```

#### Kullanıcı Silme

```bash
curl -X DELETE http://localhost:8000/admin/users/john \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Model Atama Endpoint'leri

#### Belirli Modelleri Kullanıcıya Atama

```bash
curl -X POST http://localhost:8000/admin/users/john/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["gpt-oss:120b", "deepseek-v3.1:671b", "qwen3-coder:480b"]
  }'
```

**Response:**
```json
{
  "username": "john",
  "has_all_models": false,
  "models": ["gpt-oss:120b", "deepseek-v3.1:671b", "qwen3-coder:480b"]
}
```

#### Tüm Modellere Erişim Verme

```bash
curl -X POST http://localhost:8000/admin/users/john/models/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
{
  "username": "john",
  "has_all_models": true,
  "models": []
}
```

#### Kullanıcının Modellerini Görüntüleme

```bash
curl -X GET http://localhost:8000/admin/users/john/models \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
{
  "username": "john",
  "has_all_models": false,
  "models": ["gpt-oss:120b", "deepseek-v3.1:671b"]
}
```

#### Model Erişimini Kaldırma

**Belirli bir modeli kaldırma:**
```bash
curl -X DELETE http://localhost:8000/admin/users/john/models/gpt-oss:120b \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Tüm model erişimini kaldırma:**
```bash
# "all" kullanarak tüm modelleri kaldır (has_all_models dahil)
curl -X DELETE http://localhost:8000/admin/users/john/models/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Not**: `model_name="all"` kullanıldığında:
- `has_all_models=True` ise → Tüm modellere erişim kaldırılır
- Belirli modeller atanmışsa → Tüm atanmış modeller kaldırılır
- Kullanıcı hiçbir modele erişemez hale gelir

### Model Mapping Yönetimi

#### Model Mapping Oluşturma

```bash
curl -X POST http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "gpt-oss:120b",
    "real_name": "gpt-oss:120b-cloud"
  }'
```

**Response:**
```json
{
  "display_name": "gpt-oss:120b",
  "real_name": "gpt-oss:120b-cloud",
  "created_at": "2024-01-20T10:30:00.000000"
}
```

#### Model Mapping Listesini Görüntüleme

```bash
curl -X GET http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Response:**
```json
[
  {
    "display_name": "gpt-oss:120b",
    "real_name": "gpt-oss:120b-cloud",
    "created_at": "2024-01-20T10:30:00.000000"
  },
  {
    "display_name": "deepseek-v3.1:671b",
    "real_name": "deepseek-v3.1:671b-cloud",
    "created_at": "2024-01-20T10:30:00.000000"
  }
]
```

#### Model Mapping Silme

```bash
curl -X DELETE http://localhost:8000/admin/model-mappings/gpt-oss:120b \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Not**: Model mapping ekleme/silme işlemlerinde cache otomatik olarak yenilenir.

### Model Erişim Kontrolü

Kullanıcılar yalnızca kendilerine atanmış modellere erişebilir:

- **has_all_models=true**: Kullanıcı tüm modellere erişebilir
- **has_all_models=false**: Kullanıcı sadece `models` listesindeki modellere erişebilir

Model erişim kontrolü şu endpoint'lerde çalışır:
- `/api/generate`
- `/api/chat`
- `/api/embeddings`
- `/api/show`
- `/v1/chat/completions`

Model listeleme endpoint'leri (`/api/tags`, `/v1/models`) kullanıcının erişebildiği modelleri döner.

## API Kullanımı

### Authentication

Tüm API isteklerinde `Authorization` header'ı gereklidir:

```bash
Authorization: Bearer <your-jwt-token>
```

## Cursor IDE ile Kullanım

Ollama Proxy API, OpenAI uyumlu endpoint'ler sunar, böylece Cursor IDE'de kullanabilirsiniz.

### Kurulum

1. **Cursor Settings** → **Models** → **Override OpenAI Base URL**
2. Base URL: `https://ollama.gokaygunes.com/v1`
3. API Key: `Bearer YOUR_JWT_TOKEN` (JWT token'ınızı buraya yazın)
4. Model: `gpt-oss:120b` (veya herhangi bir model ismi)

### OpenAI Compatible Endpoints

- `POST /v1/chat/completions` - Chat completions (Cursor için)
- `GET /v1/models` - Model listesi (OpenAI formatında)

### Örnek Kullanım

Cursor'da kod yazarken Ctrl+K veya Ctrl+L ile modelinizi kullanabilirsiniz. Model seçiminde Ollama modelleriniz görünecektir.

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

