"""
Database Seeder Runner - Alembic-tarzı seed migration sistemi.

Her seed dosyası app/seeds/ dizininde ayrı bir Python modülüdür.
Runner, seed_history tablosunu kontrol ederek sadece henüz çalışmamış
seed'leri sıralı şekilde çalıştırır.

Kullanım:
    python -m app.seeder              # Yeni seed'leri çalıştır
    python -m app.seeder --status     # Hangi seed'ler çalışmış göster
    python -m app.seeder --reset      # Seed history'yi temizle (veriler kalır)
    python -m app.seeder --reset-all  # Hem seed history hem seed verilerini temizle
"""

import asyncio
import argparse
import importlib
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Text, Integer,
    select, delete, text
)
from sqlalchemy.sql import func

from app.database import async_session_maker, Base

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# Seed History Tablosu (kendi kendini yöneten - Alembic version_table gibi)
# =============================================================================

class SeedHistory(Base):
    """Çalıştırılmış seed'lerin kaydı"""
    __tablename__ = "seed_history"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<SeedHistory(seed_id='{self.seed_id}')>"


# =============================================================================
# Seed Runner
# =============================================================================

SEEDS_DIR = Path(__file__).parent / "seeds"


def discover_seeds() -> list:
    """
    app/seeds/ dizinindeki seed dosyalarını keşfeder.
    NNN_*.py formatındaki dosyaları sıralı olarak döner.
    """
    if not SEEDS_DIR.exists():
        logger.warning(f"Seeds dizini bulunamadı: {SEEDS_DIR}")
        return []

    seed_files = sorted([
        f.stem for f in SEEDS_DIR.glob("[0-9][0-9][0-9]_*.py")
    ])

    return seed_files


def load_seed_module(seed_name: str):
    """Seed modülünü import eder"""
    module_path = f"app.seeds.{seed_name}"
    return importlib.import_module(module_path)


async def ensure_seed_table():
    """seed_history tablosunu oluştur (yoksa)"""
    from app.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(SeedHistory.__table__.create, checkfirst=True)


async def get_applied_seeds() -> set:
    """Daha önce çalıştırılmış seed ID'lerini getir"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(SeedHistory.seed_id)
        )
        return {row[0] for row in result.fetchall()}


async def mark_seed_applied(session, seed_id: str, description: str = None):
    """Seed'i çalıştırılmış olarak işaretle"""
    record = SeedHistory(seed_id=seed_id, description=description)
    session.add(record)


async def run_pending_seeds():
    """Henüz çalıştırılmamış seed'leri sıralı çalıştır"""
    await ensure_seed_table()

    all_seeds = discover_seeds()
    if not all_seeds:
        logger.info("📭 Seed dosyası bulunamadı.")
        return

    applied = await get_applied_seeds()
    pending = [s for s in all_seeds if s not in applied]

    if not pending:
        logger.info("✅ Tüm seed'ler zaten çalıştırılmış. Yeni seed yok.")
        return

    logger.info(f"🌱 {len(pending)} yeni seed bulundu (toplam {len(all_seeds)}, çalışmış {len(applied)})")
    logger.info("")

    for seed_name in pending:
        module = load_seed_module(seed_name)
        seed_id = getattr(module, "seed_id", seed_name)
        description = getattr(module, "description", "")

        logger.info(f"  ▶ [{seed_id}] {description}")

        async with async_session_maker() as session:
            try:
                # Seed'in run() fonksiyonunu çalıştır
                await module.run(session)
                # Başarılı → history'ye kaydet
                await mark_seed_applied(session, seed_id, description)
                await session.commit()
                logger.info(f"  ✅ [{seed_id}] Tamamlandı")
            except Exception as e:
                await session.rollback()
                logger.error(f"  ❌ [{seed_id}] Hata: {e}")
                raise

    logger.info("")
    logger.info(f"🎉 {len(pending)} seed başarıyla uygulandı!")


async def show_status():
    """Seed durumunu göster"""
    await ensure_seed_table()

    all_seeds = discover_seeds()
    applied = await get_applied_seeds()

    # Applied seed'lerin detaylarını al
    applied_details = {}
    async with async_session_maker() as session:
        result = await session.execute(
            select(SeedHistory).order_by(SeedHistory.applied_at)
        )
        for record in result.scalars().all():
            applied_details[record.seed_id] = record.applied_at

    logger.info("")
    logger.info("=" * 70)
    logger.info("🌱 Seed Durumu")
    logger.info("=" * 70)

    for seed_name in all_seeds:
        module = load_seed_module(seed_name)
        seed_id = getattr(module, "seed_id", seed_name)
        description = getattr(module, "description", "")

        if seed_id in applied:
            applied_at = applied_details.get(seed_id, "?")
            logger.info(f"  ✅ [{seed_id}] {description}")
            logger.info(f"       Uygulandı: {applied_at}")
        else:
            logger.info(f"  ⏳ [{seed_id}] {description}")
            logger.info(f"       Bekliyor")

    # Orphan seeds (dosyası silinen ama history'de kalan)
    orphans = applied - set(all_seeds)
    if orphans:
        logger.info("")
        logger.info("  ⚠️  Dosyası silinmiş seed'ler:")
        for orphan in sorted(orphans):
            logger.info(f"     🗑️  {orphan}")

    logger.info("")
    logger.info(f"Toplam: {len(all_seeds)} seed | ✅ {len(applied)} uygulanmış | ⏳ {len(all_seeds) - len(applied & set(all_seeds))} bekleyen")
    logger.info("=" * 70)
    logger.info("")


async def reset_history():
    """Seed history'yi temizle (seed verileri kalır)"""
    await ensure_seed_table()

    async with async_session_maker() as session:
        result = await session.execute(delete(SeedHistory))
        await session.commit()
        logger.info(f"🗑️  Seed history temizlendi ({result.rowcount} kayıt silindi)")
        logger.info("   Veriler yerinde kaldı. `make db-seed` ile tüm seed'ler tekrar çalıştırılabilir.")


async def reset_all():
    """Hem seed history hem de seed'lenen verileri temizle"""
    await ensure_seed_table()

    async with async_session_maker() as session:
        # Tüm seed modüllerini yükle ve downgrade varsa çalıştır
        all_seeds = discover_seeds()
        for seed_name in reversed(all_seeds):  # Ters sırada downgrade
            module = load_seed_module(seed_name)
            seed_id = getattr(module, "seed_id", seed_name)

            downgrade_fn = getattr(module, "downgrade", None)
            if downgrade_fn:
                logger.info(f"  ⏪ [{seed_id}] Geri alınıyor...")
                await downgrade_fn(session)
                logger.info(f"  ✅ [{seed_id}] Geri alındı")

        # History'yi temizle
        await session.execute(delete(SeedHistory))
        await session.commit()

    logger.info("")
    logger.info("🗑️  Tüm seed verileri ve history temizlendi.")


def main():
    parser = argparse.ArgumentParser(
        description="🌱 Ollama Proxy API - Database Seeder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python -m app.seeder              Yeni seed'leri çalıştır
  python -m app.seeder --status     Seed durumunu göster
  python -m app.seeder --reset      Seed history'yi temizle
  python -m app.seeder --reset-all  Hem history hem verileri temizle
        """
    )
    parser.add_argument("--status", action="store_true", help="Seed durumunu göster")
    parser.add_argument("--reset", action="store_true", help="Seed history'yi temizle (veriler kalır)")
    parser.add_argument("--reset-all", action="store_true", help="History + verileri temizle")

    args = parser.parse_args()

    logger.info("")
    logger.info("🌱 Ollama Proxy API - Database Seeder")
    logger.info("─" * 40)

    if args.status:
        asyncio.run(show_status())
    elif args.reset:
        asyncio.run(reset_history())
    elif args.reset_all:
        asyncio.run(reset_all())
    else:
        asyncio.run(run_pending_seeds())


if __name__ == "__main__":
    main()
