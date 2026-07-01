# System Prompt Injection — Design

> Status: **Approved (brainstorming complete)** — ready for implementation handoff.
> Scope: model / mapping / node / group bazlı system prompt tanımlama ve isteklere şeffaf otomatik enjeksiyon.

## Understanding Summary
- **Ne:** `model` / `mapping` / `node` / `group` bazlı system prompt tanımlama; istek geldiğinde eşleşen prompt'ları otomatik ve şeffaf şekilde enjekte etme.
- **Neden:** Admin'in belirli model/node/grup için davranış/politika prompt'u zerk etmesi; son kullanıcı (Cursor, Claude Code, Antigravity vb.) farkında olmadan.
- **Kim için:** Admin tanımlar → son kullanıcı habersiz uygular.
- **4 scope:** mapping = `display_name`, model = `real_name`, node, group.
- **Birleştirme:** Eşleşen tüm prompt'lar priority ile stacklenir → kullanıcının kendi system'ıyla **tek** system mesajında birleşir.
- **Enjeksiyon noktası:** `proxy_request` içinde (group/mapping/node çözüldükten sonra, göndermeden önce) → merkezi olduğu için tüm metin uçları otomatik kapsanır.

## Assumptions
- **A1 — Cache:** Prompt'lar **Redis**'te cache'lenir (auth `USER_ACCESS` pattern'i). Tek key'de tüm aktif prompt'lar; istekte 1 GET + process içi filtre. Admin mutasyonunda invalidate; TTL güvenlik ağı. Redis yoksa DB'ye graceful fallback. (In-memory reddedildi — çok worker'da tutarsız.)
- **A2 — Kapsam:** `proxy_request`'ten geçen metin uçları: `/v1/chat/completions`, `/api/chat` (messages), `/api/generate` (`system` alanı). Embeddings/tags hariç.
- **A3 — Sıralama:** `priority DESC` birincil; eşitlikte sabit scope sırası **node → group → model → mapping**. Admin politikası kullanıcının system içeriğinin **önünde**.
- **A4 — Depolama:** Tek esnek tablo `system_prompts`.
- **A5 — Yönetim:** Admin API + frontend CRUD sayfası; mutasyonlar `AuditLog`'a yazılır.
- **A6 — Güvenli varsayılan:** Eşleşme yoksa istek hiç dokunulmadan geçer. Boş/pasif kayıtlar atlanır.
- **A7 — Şeffaflık:** Sadece giden isteğe eklenir; client'a dönmez.

## Decision Log
| # | Karar | Seçim | Alternatifler | Gerekçe |
|---|---|---|---|---|
| 1 | Çoklu scope eşleşmesi | Eşleşenlerin hepsi stacklenir | Tek en-spesifik / mod-bazlı | Katmanlı prompt esnekliği |
| 2 | Kullanıcı system mesajı | Bizimkiyle birleştirilir (tek system) | Ayrı ekle / ez | Kullanıcı fark etmez, katı modellerde tek-system uyumu |
| 3 | Model vs Mapping | Ayrı scope: mapping=display_name, model=real_name | Aynı / ham dahil | İki farklı granülarite gerçek ihtiyaç |
| 4 | Cache | Redis (tek key + invalidate + TTL) | In-memory | Çok-worker tutarlılığı |
| 5 | Sıralama | priority DESC → scope sırası (node→group→model→mapping) | scope-önce | Admin'e net kontrol |
| 6 | Depolama | Tek tablo, `UNIQUE(scope_type, scope_value)` | Hedef başına çok kayıt / kolon-bazlı | Yönetim netliği + YAGNI |
| 7 | Node eşleşmesi | name / code / id'den biri | sadece id | Esneklik (OQ2) |
| 8 | Kapsam | Tüm metin uçları (chat + generate) | sadece chat | Tutarlılık (OQ1) |
| 9 | Navigation | Ayrı üst-seviye "System Prompts" | Models altı sekme | Çapraz konu, keşfedilebilirlik |

## Final Design

### 1. Veri Modeli
```
system_prompts
├─ id            PK
├─ scope_type    'model' | 'mapping' | 'node' | 'group'
├─ scope_value   mapping→display_name, model→real_name, group→grup adı, node→name/code/id
├─ prompt        Text
├─ priority      Integer default 0  (yüksek = daha önce)
├─ is_active     Boolean default true
├─ description   Text? (admin notu)
├─ created_at / updated_at
└─ UNIQUE(scope_type, scope_value)
```
Bir istek en fazla 1 mapping + 1 model + 1 group + 1 node = 4 prompt'u stackler.

### 2. Eşleştirme & Enjeksiyon
Enjeksiyon anında bilinen değerler: mapping→`original_model`, model→çözülmüş `real_name`, group→`original_group`, node→seçili node `name/code/id`.

1. Redis'ten aktifleri al → eşleşenleri topla.
2. Sırala: `priority DESC` → scope sırası (node→group→model→mapping).
3. Metinleri `\n\n` ile birleştir → `injected_block`.
4. Merge:
   - **Chat:** ilk `system` mesajı varsa `injected_block + "\n\n" + mevcut`; yoksa başa yeni system mesajı. Diğer mesajlar değişmez.
   - **Generate:** `data['system'] = injected_block (+ "\n\n" + varsa mevcut)`.
5. Edge: eşleşme yok → dokunma. System içeriği string değilse (multimodal) → ayrı leading system mesajı (güvenli fallback).

### 3. Cache & Katmanlar
- Redis key `system_prompts:active` (JSON list). Miss→DB+doldur (TTL 600s). Mutasyon→invalidate. Redis-down→DB fallback.
- Katmanlar:
  - `app/repositories/system_prompt_repository.py` — CRUD + `get_all_active()`
  - `app/services/system_prompt_service.py` — `get_active_prompts()` (cached), `invalidate_cache()`, `build_injection(ctx)`
  - `app/proxy.py` — `proxy_request` içinde `build_injection(...)` + system merge
  - `alembic/versions/xxxx_system_prompts.py` — migration

### 4. Admin API & Frontend
- **Router `/admin/system-prompts`** (verify_admin): `GET /`, `POST /`, `PATCH /{id}`, `DELETE /{id}`. Her mutasyon → invalidate + AuditLog. `(scope_type, scope_value)` çakışması → 409.
- Pydantic: `SystemPromptCreate`, `SystemPromptUpdate`, `SystemPromptResponse`.
- **Frontend `frontend/src/app/system-prompts/page.tsx`** (ayrı nav öğesi): scope_type'a göre gruplu liste; oluştur/düzenle dialog'unda scope_type select + akıllı scope_value seçici (mapping→catalog, model→node modelleri, group→groups, node→nodes), prompt textarea, priority, description, is_active. `systemPromptsApi` + react-query.

## Known Limitations (v1)
- **Failover tek-sefer enjeksiyon:** Enjeksiyon ilk çözümlemeye göre bir kez yapılır; failover model/node değiştirirse yeniden hesaplanmaz. Sonradan `_rebind_body_to_node` içinde tekrar-hesaplama ile iyileştirilebilir.
- Hedef başına tek kayıt; tam serbest çok-kayıt stacking şimdilik yok (gerekirse UNIQUE kaldırılıp açılır).

## Implementation Outline
1. DB model (`models_db.py`) + Alembic migration.
2. Repository + Service (Redis cache + matching/build).
3. `proxy_request` entegrasyonu (chat + generate merge) + edge case'ler.
4. Admin router + Pydantic modelleri + AuditLog + invalidate.
5. Frontend: api client, nav öğesi, liste + dialog.
6. Testler: service matching/sıralama birim testleri; proxy merge testi (chat + generate + no-match + mevcut-system).
