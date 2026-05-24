"""
Seed: Model config'leri (hardcoded tool filtering + param restrictions → DB)
"""

from sqlalchemy import select, delete
from app.models_db import ModelConfig

seed_id = "002_initial_model_configs"
description = "Hardcoded model config'lerini DB'ye taşı (tool filtering, param restrictions)"

MODEL_CONFIGS = [
    {
        "model_prefix": "minimax",
        "allowed_tools": [
            "Read", "Write", "Shell", "Glob", "Grep", "StrReplace",
            "Delete", "TodoWrite", "WebSearch", "WebFetch",
            "SemanticSearch", "ReadLints",
        ],
        "unsupported_params": None,
        "default_context_length": 204800,
        "description": "Minimax modelleri - sadeleştirilmiş tool set (Ollama 500 önlemek için)",
    },
    {
        "model_prefix": "deepseek",
        "allowed_tools": None,
        "unsupported_params": ["tools", "tool_choice"],
        "default_context_length": 163840,
        "description": "Deepseek modelleri - tool calling devre dışı (Ollama tool call instability)",
    },
    {
        "model_prefix": "kimi",
        "allowed_tools": None,
        "unsupported_params": ["top_p"],
        "default_context_length": 262144,
        "description": "Kimi modelleri - top_p uyumsuzluğu, custom tool call format desteği",
    },
    {
        "model_prefix": "gemini",
        "allowed_tools": None,
        "unsupported_params": ["top_p", "presence_penalty", "frequency_penalty"],
        "default_context_length": 131072,
        "description": "Gemini modelleri - bazı parametreler Ollama ile uyumsuz",
    },
    {
        "model_prefix": "qwen3",
        "allowed_tools": None,
        "unsupported_params": None,
        "default_context_length": 262144,
        "description": "Qwen3 modelleri",
    },
    {
        "model_prefix": "glm",
        "allowed_tools": None,
        "unsupported_params": None,
        "default_context_length": 202752,
        "description": "GLM modelleri",
    },
    {
        "model_prefix": "mistral",
        "allowed_tools": None,
        "unsupported_params": None,
        "default_context_length": 262144,
        "description": "Mistral modelleri",
    },
    {
        "model_prefix": "cogito",
        "allowed_tools": None,
        "unsupported_params": None,
        "default_context_length": 163840,
        "description": "Cogito modelleri",
    },
    {
        "model_prefix": "composer",
        "allowed_tools": None,
        "unsupported_params": None,
        "default_context_length": 200000,
        "description": "Cursor Composer modelleri (composer-2.5 vb.)",
    },
]


async def run(session):
    """Model config'leri ekle (var olanları atla)"""
    count = 0
    for mc in MODEL_CONFIGS:
        existing = await session.execute(
            select(ModelConfig).where(ModelConfig.model_prefix == mc["model_prefix"])
        )
        if existing.scalar_one_or_none():
            continue

        session.add(ModelConfig(
            model_prefix=mc["model_prefix"],
            allowed_tools=mc["allowed_tools"],
            unsupported_params=mc["unsupported_params"],
            default_context_length=mc.get("default_context_length", 32768),
            description=mc.get("description"),
        ))
        count += 1

    await session.flush()
    print(f"    → {count} yeni model config eklendi ({len(MODEL_CONFIGS)} toplam)")


async def downgrade(session):
    """Bu seed'in eklediği config'leri sil"""
    prefixes = [mc["model_prefix"] for mc in MODEL_CONFIGS]
    await session.execute(
        delete(ModelConfig).where(ModelConfig.model_prefix.in_(prefixes))
    )
    await session.flush()
    print(f"    → {len(prefixes)} model config silindi")
