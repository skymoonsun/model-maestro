# Hızlı Başlangıç Kılavuzu

## 1. Servisi Başlatın

```bash
# Docker Compose ile servisi başlatın
docker-compose up -d

# Logları kontrol edin
docker-compose logs -f
```

## 2. İlk Kullanıcıyı Oluşturun

```bash
# Kullanıcı oluştur
docker exec ollama-proxy create-user admin

# Token'ı kaydedin (çıktıda gösterilecek)
```

## 3. API'yi Test Edin

### Health Check

```bash
curl http://localhost:8000/health
```

### Model Listesi (Authentication gerekli)

```bash
# TOKEN değişkenini yukarıda aldığınız token ile değiştirin
export TOKEN="your-jwt-token-here"

curl -X GET http://localhost:8000/api/tags \
  -H "Authorization: Bearer $TOKEN"
```

### Chat Request

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [
      {"role": "user", "content": "Merhaba!"}
    ]
  }'
```

## 4. CLI Komutları

### Tüm Kullanıcıları Listele

```bash
docker exec ollama-proxy list-users
```

### Kullanıcı Bilgilerini Gör

```bash
docker exec ollama-proxy show-user admin
```

### Token'ı Yenile

```bash
docker exec ollama-proxy refresh-token admin
```

### Yeni Kullanıcı Ekle

```bash
docker exec ollama-proxy create-user developer
```

### Kullanıcı Sil

```bash
docker exec ollama-proxy delete-user developer
```

## 5. Model Mapping Ekleme

Yeni bir cloud model eklemek için `config/model_mappings.json` dosyasını düzenleyin:

```json
{
  "mappings": {
    "gpt-oss:120b": "gpt-oss:120b-cloud",
    "gpt-oss:20b": "gpt-oss:20b-cloud",
    "yeni-model:versyon": "yeni-model:versyon-cloud"
  }
}
```

Değişiklikleri uygulamak için servisi yeniden başlatın:

```bash
docker-compose restart
```

## 6. Sorun Giderme

### Servisi Durdur

```bash
docker-compose down
```

### Servisi Yeniden Başlat

```bash
docker-compose restart
```

### Logları İzle

```bash
docker-compose logs -f ollama-proxy
```

### Container İçine Gir

```bash
docker exec -it ollama-proxy /bin/bash
```

## Önemli Notlar

⚠️ **Güvenlik**
- `.env` dosyasındaki `JWT_SECRET_KEY` değerini mutlaka değiştirin
- Token'ları güvenli bir şekilde saklayın
- Production'da HTTPS kullanın

📝 **Model İsimleri**
- Client'tan: `gpt-oss:120b` (cloud suffix olmadan)
- Ollama'ya: `gpt-oss:120b-cloud` (otomatik eklenir)
- Yanıtta: `gpt-oss:120b` (otomatik kaldırılır)

🔄 **Endpoint'ler**
- Tüm Ollama API endpoint'leri desteklenir
- Her istek JWT authentication gerektirir
- Streaming desteklenir

