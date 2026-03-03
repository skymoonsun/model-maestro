"""
Seed: İlk model mapping'leri
"""

from sqlalchemy import select, delete
from app.models_db import ModelMapping

seed_id = "001_initial_model_mappings"
description = "İlk model mapping'lerini seed et (26 model)"

MODEL_MAPPINGS = [
    {"display_name": "rnj-1:8b", "real_name": "rnj-1:8b-cloud", "context_length": 32768},
    {"display_name": "devstral-2:123b", "real_name": "devstral-2:123b-cloud", "context_length": 262144},
    {"display_name": "minimax-m2.1:latest", "real_name": "minimax-m2.1:cloud", "context_length": 204800},
    {"display_name": "nemotron-3-nano:30b", "real_name": "nemotron-3-nano:30b-cloud", "context_length": 1048576},
    {"display_name": "glm-4.7:latest", "real_name": "glm-4.7:cloud", "context_length": 202752},
    {"display_name": "deepseek-v3.2:latest", "real_name": "deepseek-v3.2:cloud", "context_length": 163840},
    {"display_name": "gemma3:27b", "real_name": "gemma3:27b-cloud", "context_length": 131072},
    {"display_name": "mistral-large-3:675b", "real_name": "mistral-large-3:675b-cloud", "context_length": 262144},
    {"display_name": "ministral-3:14b", "real_name": "ministral-3:14b-cloud", "context_length": 262144},
    {"display_name": "qwen3-coder:480b", "real_name": "qwen3-coder:480b-cloud", "context_length": 262144},
    {"display_name": "deepseek-v3.1:671b", "real_name": "deepseek-v3.1:671b-cloud", "context_length": 163840},
    {"display_name": "gpt-oss:120b", "real_name": "gpt-oss:120b-cloud", "context_length": 131072},
    {"display_name": "kimi-k2:1t", "real_name": "kimi-k2:1t-cloud", "context_length": 262144},
    {"display_name": "glm-4.6:latest", "real_name": "glm-4.6:cloud", "context_length": 202752},
    {"display_name": "qwen3-vl:235b", "real_name": "qwen3-vl:235b-cloud", "context_length": 262144},
    {"display_name": "gpt-oss:20b", "real_name": "gpt-oss:20b-cloud", "context_length": 131072},
    {"display_name": "minimax-m2:latest", "real_name": "minimax-m2:cloud", "context_length": 204800},
    {"display_name": "kimi-k2-thinking:latest", "real_name": "kimi-k2-thinking:cloud", "context_length": 262144},
    {"display_name": "qwen3-vl:235b-instruct", "real_name": "qwen3-vl:235b-instruct-cloud", "context_length": 262144},
    {"display_name": "cogito-2.1:671b", "real_name": "cogito-2.1:671b-cloud", "context_length": 163840},
    {"display_name": "kimi-k2.5:latest", "real_name": "kimi-k2.5:cloud", "context_length": 262144},
    {"display_name": "qwen3.5:latest", "real_name": "qwen3.5:cloud", "context_length": 262144},
    {"display_name": "qwen3.5:397b", "real_name": "qwen3.5:397b-cloud", "context_length": 262144},
    {"display_name": "minimax-m2.5:latest", "real_name": "minimax-m2.5:cloud", "context_length": 202752},
    {"display_name": "qwen3-coder-next:latest", "real_name": "qwen3-coder-next:cloud", "context_length": 262144},
    {"display_name": "glm-5:latest", "real_name": "glm-5:cloud", "context_length": 196608},
]


async def run(session):
    """Model mapping'leri ekle (var olanları atla)"""
    count = 0
    for m in MODEL_MAPPINGS:
        existing = await session.execute(
            select(ModelMapping).where(ModelMapping.display_name == m["display_name"])
        )
        if existing.scalar_one_or_none():
            continue

        session.add(ModelMapping(
            display_name=m["display_name"],
            real_name=m["real_name"],
            context_length=m["context_length"],
        ))
        count += 1

    await session.flush()
    print(f"    → {count} yeni mapping eklendi ({len(MODEL_MAPPINGS)} toplam)")


async def downgrade(session):
    """Bu seed'in eklediği mapping'leri sil"""
    display_names = [m["display_name"] for m in MODEL_MAPPINGS]
    await session.execute(
        delete(ModelMapping).where(ModelMapping.display_name.in_(display_names))
    )
    await session.flush()
    print(f"    → {len(display_names)} mapping silindi")
