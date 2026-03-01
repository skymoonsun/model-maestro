"""Repository for ModelConfig operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional, List

from app.models_db import ModelConfig


class ModelConfigRepository:
    """Repository for ModelConfig CRUD operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_all(self) -> List[ModelConfig]:
        """Get all model configs"""
        stmt = select(ModelConfig).order_by(ModelConfig.model_prefix)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_active(self) -> List[ModelConfig]:
        """Get all active model configs"""
        stmt = select(ModelConfig).where(
            ModelConfig.is_active == True
        ).order_by(ModelConfig.model_prefix)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_by_id(self, config_id: int) -> Optional[ModelConfig]:
        """Get model config by ID"""
        stmt = select(ModelConfig).where(ModelConfig.id == config_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_prefix(self, model_prefix: str) -> Optional[ModelConfig]:
        """Get model config by prefix"""
        stmt = select(ModelConfig).where(ModelConfig.model_prefix == model_prefix)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create(self, **kwargs) -> ModelConfig:
        """Create a new model config"""
        config = ModelConfig(**kwargs)
        self.session.add(config)
        await self.session.flush()
        return config
    
    async def update(self, config_id: int, **kwargs) -> Optional[ModelConfig]:
        """Update an existing model config"""
        config = await self.get_by_id(config_id)
        if not config:
            return None
        
        for key, value in kwargs.items():
            if hasattr(config, key) and key not in ('id', 'created_at'):
                setattr(config, key, value)
        
        await self.session.flush()
        return config
    
    async def delete_by_id(self, config_id: int) -> bool:
        """Delete a model config by ID"""
        stmt = delete(ModelConfig).where(ModelConfig.id == config_id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
    
    async def find_config_for_model(self, model_name: str) -> Optional[ModelConfig]:
        """Find the matching config for a model name using prefix matching"""
        configs = await self.get_active()
        model_name_lower = model_name.lower()
        
        # Check for exact matches first
        for config in configs:
            if config.is_exact_match and config.model_prefix.lower() == model_name_lower:
                return config
        
        # Find the best matching prefix (longest match first) among prefix-configs
        best_match = None
        for config in configs:
            if not config.is_exact_match and model_name_lower.startswith(config.model_prefix.lower()):
                if best_match is None or len(config.model_prefix) > len(best_match.model_prefix):
                    best_match = config
        
        return best_match
