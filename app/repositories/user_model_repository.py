"""User-Model relationship repository"""

from typing import List, Optional
from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_db import UserModel, User


class UserModelRepository:
    """Repository for UserModel operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def assign_model(self, user_id: int, model_display_name: str) -> UserModel:
        """Assign a specific model to user"""
        user_model = UserModel(
            user_id=user_id,
            model_display_name=model_display_name,
            has_all_models=False
        )
        self.session.add(user_model)
        await self.session.commit()
        await self.session.refresh(user_model)
        return user_model
    
    async def grant_all_models(self, user_id: int) -> UserModel:
        """Grant access to all models"""
        # First, remove any existing specific model assignments
        await self.session.execute(
            delete(UserModel).where(UserModel.user_id == user_id)
        )
        
        # Create "all models" entry
        user_model = UserModel(
            user_id=user_id,
            model_display_name=None,
            has_all_models=True
        )
        self.session.add(user_model)
        await self.session.commit()
        await self.session.refresh(user_model)
        return user_model
    
    async def get_user_models(self, user_id: int) -> List[str]:
        """Get list of models user can access"""
        result = await self.session.execute(
            select(UserModel).where(UserModel.user_id == user_id)
        )
        user_models = result.scalars().all()
        
        # If has_all_models, return special indicator
        for um in user_models:
            if um.has_all_models:
                return []  # Empty list means all models
        
        # Return list of specific models
        return [um.model_display_name for um in user_models if um.model_display_name]
    
    async def has_all_models(self, user_id: int) -> bool:
        """Check if user has access to all models"""
        result = await self.session.execute(
            select(UserModel).where(
                and_(UserModel.user_id == user_id, UserModel.has_all_models == True)
            )
        )
        return result.scalar_one_or_none() is not None
    
    async def has_model_access(self, user_id: int, model_display_name: str) -> bool:
        """Check if user has access to specific model"""
        # Check if has all models
        if await self.has_all_models(user_id):
            return True
        
        # Check if has specific model
        result = await self.session.execute(
            select(UserModel).where(
                and_(
                    UserModel.user_id == user_id,
                    UserModel.model_display_name == model_display_name
                )
            )
        )
        return result.scalar_one_or_none() is not None
    
    async def revoke_model(self, user_id: int, model_display_name: str) -> bool:
        """Revoke access to specific model"""
        result = await self.session.execute(
            delete(UserModel).where(
                and_(
                    UserModel.user_id == user_id,
                    UserModel.model_display_name == model_display_name
                )
            )
        )
        await self.session.commit()
        return result.rowcount > 0
    
    async def revoke_all_models(self, user_id: int) -> bool:
        """Revoke all model access"""
        result = await self.session.execute(
            delete(UserModel).where(UserModel.user_id == user_id)
        )
        await self.session.commit()
        return result.rowcount > 0
    
    async def delete_all_for_user(self, user_id: int) -> bool:
        """Alias for revoke_all_models - delete all user_models entries for a user"""
        return await self.revoke_all_models(user_id)
    
    async def assign_multiple_models(self, user_id: int, model_names: List[str]) -> List[UserModel]:
        """Assign multiple models to user"""
        # First, remove all existing assignments
        await self.revoke_all_models(user_id)
        
        # Add new assignments
        user_models = []
        for model_name in model_names:
            um = UserModel(
                user_id=user_id,
                model_display_name=model_name,
                has_all_models=False
            )
            self.session.add(um)
            user_models.append(um)
        
        await self.session.commit()
        return user_models

