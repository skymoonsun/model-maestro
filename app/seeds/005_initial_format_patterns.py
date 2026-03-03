"""
Seed: Model format pattern'leri (Kimi custom tool call patterns)
"""

from sqlalchemy import select, delete, and_
from app.models_db import ModelFormatPattern

seed_id = "005_initial_format_patterns"
description = "Kimi/Moonshot custom tool call format pattern'lerini seed et"

FORMAT_PATTERNS = [
    {
        "model_prefix": "kimi",
        "format_type": "custom_tool_call",
        "pattern_config": {
            "section_start": "<|tool_calls_section_begin|>",
            "section_end": "<|tool_calls_section_end|>",
            "call_start": "<|tool_call_begin|>",
            "call_end": "<|tool_call_end|>",
            "arg_start": "<|tool_call_argument_begin|>",
        },
    },
    {
        "model_prefix": "moonshot",
        "format_type": "custom_tool_call",
        "pattern_config": {
            "section_start": "<|tool_calls_section_begin|>",
            "section_end": "<|tool_calls_section_end|>",
            "call_start": "<|tool_call_begin|>",
            "call_end": "<|tool_call_end|>",
            "arg_start": "<|tool_call_argument_begin|>",
        },
    },
]


async def run(session):
    """Format pattern'leri ekle (var olanları atla)"""
    count = 0
    for fp in FORMAT_PATTERNS:
        existing = await session.execute(
            select(ModelFormatPattern).where(
                and_(
                    ModelFormatPattern.model_prefix == fp["model_prefix"],
                    ModelFormatPattern.format_type == fp["format_type"],
                )
            )
        )
        if existing.scalar_one_or_none():
            continue

        session.add(ModelFormatPattern(
            model_prefix=fp["model_prefix"],
            format_type=fp["format_type"],
            pattern_config=fp["pattern_config"],
            is_active=True,
        ))
        count += 1

    await session.flush()
    print(f"    → {count} yeni format pattern eklendi ({len(FORMAT_PATTERNS)} toplam)")


async def downgrade(session):
    """Bu seed'in eklediği pattern'leri sil"""
    for fp in FORMAT_PATTERNS:
        await session.execute(
            delete(ModelFormatPattern).where(
                and_(
                    ModelFormatPattern.model_prefix == fp["model_prefix"],
                    ModelFormatPattern.format_type == fp["format_type"],
                )
            )
        )
    await session.flush()
    print(f"    → {len(FORMAT_PATTERNS)} format pattern silindi")
