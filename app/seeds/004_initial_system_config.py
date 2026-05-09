"""
Seed: Sistem konfigürasyonu
"""

from sqlalchemy import select, delete
from app.models_db import SystemConfig

seed_id = "004_initial_system_config"
description = "Sistem konfigürasyonunu seed et (background tasks, http client, defaults, ollama params)"

SYSTEM_CONFIGS = {
    # Background Tasks
    "background_tasks.batch_size": ("50", "Her batch'te işlenecek aktivite log sayısı"),
    "background_tasks.poll_interval": ("2.0", "Background processor polling aralığı (saniye)"),
    "background_tasks.queue_key": ("activity_log_queue", "Redis kuyruk anahtarı"),

    # HTTP Client
    "http_client.max_connections": ("100", "HTTP connection pool - max bağlantı sayısı"),
    "http_client.max_keepalive_connections": ("40", "HTTP keepalive - max bağlantı sayısı"),
    "http_client.keepalive_expiry": ("300", "HTTP keepalive süresi (saniye)"),
    "http_client.timeout": ("1200", "HTTP istek zaman aşımı (saniye)"),

    # Defaults
    "defaults.context_length": ("32768", "DB'de tanımsız modeller için varsayılan context uzunluğu"),
    "defaults.log_level": ("INFO", "Uygulama log seviyesi"),

    # Search
    "search.web_search_url": (
        "https://ollama.com/api/web_search",
        "Web search backend URL (Ollama Web Search, DuckDuckGo proxy, etc.)"
    ),
    "search.web_search_api_key": (
        "",
        "Web search backend API key"
    ),

    # Ollama Unsupported Params
    "ollama_unsupported_params": (
        '["logit_bias","logprobs","top_logprobs","top_k","response_format","user","service_tier","parallel_tool_calls","store","metadata","prediction","modalities","audio"]',
        "Ollama'nın desteklemediği OpenAI parametreleri (JSON array)"
    ),
}


async def run(session):
    """Sistem config'lerini ekle (var olanları atla)"""
    count = 0
    for key, (value, description) in SYSTEM_CONFIGS.items():
        existing = await session.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
        if existing.scalar_one_or_none():
            continue

        session.add(SystemConfig(
            key=key,
            value=value,
            description=description,
        ))
        count += 1

    await session.flush()
    print(f"    → {count} yeni config eklendi ({len(SYSTEM_CONFIGS)} toplam)")


async def downgrade(session):
    """Bu seed'in eklediği config'leri sil"""
    keys = list(SYSTEM_CONFIGS.keys())
    await session.execute(
        delete(SystemConfig).where(SystemConfig.key.in_(keys))
    )
    await session.flush()
    print(f"    → {len(keys)} config silindi")
