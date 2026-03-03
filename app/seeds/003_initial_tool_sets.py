"""
Seed: Tool set'leri
"""

from sqlalchemy import select, delete
from app.models_db import ToolSet

seed_id = "003_initial_tool_sets"
description = "Ön tanımlı tool set'leri oluştur (full, standard, extended, basic, minimal, readonly)"

TOOL_SETS = [
    {
        "name": "full",
        "tools": None,
        "description": "Tüm araçlar (kısıtlama yok)",
    },
    {
        "name": "standard",
        "tools": [
            "Read", "Write", "Shell", "Glob", "Grep",
            "StrReplace", "Delete", "TodoWrite", "WebSearch",
        ],
        "description": "Standart araçlar - genel kullanım",
    },
    {
        "name": "extended",
        "tools": [
            "Read", "Write", "Shell", "Glob", "Grep",
            "StrReplace", "Delete", "TodoWrite",
            "WebSearch", "WebFetch", "SemanticSearch", "ReadLints",
        ],
        "description": "Genişletilmiş araçlar - minimax uyumlu set",
    },
    {
        "name": "basic",
        "tools": ["Read", "Write", "Shell", "Glob", "Grep"],
        "description": "Temel araçlar - minimum set",
    },
    {
        "name": "minimal",
        "tools": ["Read", "Write"],
        "description": "Minimum araçlar - sadece okuma/yazma",
    },
    {
        "name": "readonly",
        "tools": ["Read", "Glob", "Grep"],
        "description": "Sadece okuma araçları",
    },
]


async def run(session):
    """Tool set'leri ekle (var olanları atla)"""
    count = 0
    for ts in TOOL_SETS:
        existing = await session.execute(
            select(ToolSet).where(ToolSet.name == ts["name"])
        )
        if existing.scalar_one_or_none():
            continue

        session.add(ToolSet(
            name=ts["name"],
            tools=ts["tools"],
            description=ts.get("description"),
        ))
        count += 1

    await session.flush()
    print(f"    → {count} yeni tool set eklendi ({len(TOOL_SETS)} toplam)")


async def downgrade(session):
    """Bu seed'in eklediği tool set'leri sil"""
    names = [ts["name"] for ts in TOOL_SETS]
    await session.execute(
        delete(ToolSet).where(ToolSet.name.in_(names))
    )
    await session.flush()
    print(f"    → {len(names)} tool set silindi")
