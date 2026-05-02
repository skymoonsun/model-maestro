"""User repository for database operations"""

from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_db import User


class UserRepository:
    """Repository for User operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, username: str, token: str) -> User:
        """Create a new user"""
        user = User(username=username, token=token)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user
    
    async def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username"""
        result = await self.session.execute(
            select(User).where(User.username == username, User.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_by_usernames(self, usernames: List[str]) -> List[User]:
        """Bulk get users by username list"""
        if not usernames:
            return []
        result = await self.session.execute(
            select(User).where(User.username.in_(usernames), User.is_active == True)
        )
        return result.scalars().all()
    
    async def get_by_token(self, token: str) -> Optional[User]:
        """Get user by token"""
        result = await self.session.execute(
            select(User).where(User.token == token, User.is_active == True)
        )
        return result.scalar_one_or_none()
    
    async def list_all(self, include_inactive: bool = False) -> List[User]:
        """List all users"""
        query = select(User)
        if not include_inactive:
            query = query.where(User.is_active == True)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def update_token(self, username: str, new_token: str) -> Optional[User]:
        """Update user token"""
        user = await self.get_by_username(username)
        if not user:
            return None
        
        user.token = new_token
        await self.session.commit()
        await self.session.refresh(user)
        return user
    
    async def soft_delete(self, username: str) -> bool:
        """Soft delete user (set is_active=False)"""
        user = await self.get_by_username(username)
        if not user:
            return False
        
        user.is_active = False
        await self.session.commit()
        return True
    
    async def delete(self, username: str) -> bool:
        """Hard delete user"""
        user = await self.get_by_username(username)
        if not user:
            return False
        
        await self.session.delete(user)
        await self.session.commit()
        return True
    
    async def exists(self, username: str) -> bool:
        """Check if user exists (active users only)"""
        result = await self.session.execute(
            select(User.id).where(User.username == username, User.is_active == True)
        )
        return result.scalar_one_or_none() is not None
    
    async def get_by_username_any(self, username: str) -> Optional[User]:
        """Get user by username (including inactive users)"""
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()
    
    async def reactivate(self, username: str, new_token: str) -> Optional[User]:
        """Reactivate a soft-deleted user with new token"""
        user = await self.get_by_username_any(username)
        if not user:
            return None
        
        user.is_active = True
        user.token = new_token
        await self.session.commit()
        await self.session.refresh(user)
        return user

