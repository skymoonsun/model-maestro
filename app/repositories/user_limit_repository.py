"""Repository for UserLimit operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.models_db import UserLimit


class UserLimitRepository:
    """Repository for UserLimit operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_user_limit(self, user_id: int) -> Optional[UserLimit]:
        """Get user limits"""
        stmt = select(UserLimit).where(UserLimit.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def set_user_limit(
        self,
        user_id: int,
        request_limit: Optional[int] = None,
        token_limit: Optional[int] = None
    ) -> UserLimit:
        """Set user limits"""
        # Check if limit already exists
        existing_limit = await self.get_user_limit(user_id)
        
        if existing_limit:
            # Update existing limit
            existing_limit.request_limit = request_limit
            existing_limit.token_limit = token_limit
            await self.session.flush()
            return existing_limit
        else:
            # Create new limit
            user_limit = UserLimit(
                user_id=user_id,
                request_limit=request_limit,
                token_limit=token_limit
            )
            self.session.add(user_limit)
            await self.session.flush()
            return user_limit
    
    async def remove_user_limit(self, user_id: int) -> bool:
        """Remove user limits"""
        user_limit = await self.get_user_limit(user_id)
        if user_limit:
            self.session.delete(user_limit)
            return True
        return False
