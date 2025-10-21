"""User management and JWT token operations (PostgreSQL-based)"""

from datetime import datetime
from typing import Optional, List
import jwt

from app.config import get_settings
from app.database import async_session_maker
from app.repositories import UserRepository, UserModelRepository


class UserManager:
    """Manage users and JWT tokens with PostgreSQL"""
    
    def __init__(self):
        self.settings = get_settings()
    
    def _generate_token(self, username: str) -> str:
        """Generate JWT token for user (no expiration)"""
        payload = {
            "username": username,
            "iat": int(datetime.utcnow().timestamp())
        }
        token = jwt.encode(payload, self.settings.jwt_secret_key, algorithm="HS256")
        return token
    
    async def create_user(self, username: str) -> Optional[dict]:
        """
        Create a new user with JWT token
        
        If user exists but is inactive (soft deleted), reactivate with new token
        """
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            
            # Check if active user already exists
            if await user_repo.exists(username):
                raise ValueError(f"Kullanıcı zaten mevcut: {username}")
            
            # Check if inactive user exists (soft deleted)
            inactive_user = await user_repo.get_by_username_any(username)
            if inactive_user and not inactive_user.is_active:
                # Reactivate user with new token
                new_token = self._generate_token(username)
                user = await user_repo.reactivate(username, new_token)
                
                return {
                    "username": user.username,
                    "token": user.token,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                    "reactivated": True
                }
            
            # Generate token
            token = self._generate_token(username)
            
            # Create new user
            user = await user_repo.create(username, token)
            
            return {
                "username": user.username,
                "token": user.token,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                "reactivated": False
            }
    
    async def delete_user(self, username: str) -> bool:
        """
        Delete a user (soft delete)
        
        Also deletes all user_models entries (hard delete)
        """
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user_model_repo = UserModelRepository(session)
            
            # Get user first
            user = await user_repo.get_by_username(username)
            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")
            
            # Hard delete all user_models entries
            await user_model_repo.delete_all_for_user(user.id)
            
            # Soft delete user
            result = await user_repo.soft_delete(username)
            
            return result
    
    async def refresh_token(self, username: str) -> Optional[dict]:
        """Refresh user's JWT token"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            
            # Generate new token
            new_token = self._generate_token(username)
            
            # Update user
            user = await user_repo.update_token(username, new_token)
            
            if not user:
                return None
            
            return {
                "username": user.username,
                "token": user.token,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "updated_at": user.updated_at.isoformat() if user.updated_at else None
            }
    
    async def get_user(self, username: str) -> Optional[dict]:
        """Get user by username"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            return {
                "username": user.username,
                "token": user.token,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                "is_active": user.is_active
            }
    
    async def list_users(self) -> List[dict]:
        """List all users"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            users = await user_repo.list_all()
            
            return [
                {
                    "username": user.username,
                    "token": user.token,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                    "is_active": user.is_active
                }
                for user in users
            ]
    
    async def verify_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return username"""
        try:
            payload = jwt.decode(token, self.settings.jwt_secret_key, algorithms=["HS256"])
            username = payload.get("username")
            
            if not username:
                return None
            
            # Check if user exists and token matches in DB
            async with async_session_maker() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_token(token)
                
                if user and user.username == username:
                    return username
            
            return None
        except jwt.InvalidTokenError:
            return None
        except Exception as e:
            print(f"Error verifying token: {e}")
            return None
    
    async def assign_models_to_user(self, username: str, models: List[str]) -> bool:
        """Assign specific models to user"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return False
            
            user_model_repo = UserModelRepository(session)
            await user_model_repo.assign_multiple_models(user.id, models)
            
            return True
    
    async def grant_all_models(self, username: str) -> bool:
        """Grant access to all models"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return False
            
            user_model_repo = UserModelRepository(session)
            await user_model_repo.grant_all_models(user.id)
            
            return True
    
    async def get_user_models(self, username: str) -> Optional[dict]:
        """Get user's assigned models"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            user_model_repo = UserModelRepository(session)
            has_all = await user_model_repo.has_all_models(user.id)
            models = await user_model_repo.get_user_models(user.id)
            
            return {
                "username": username,
                "has_all_models": has_all,
                "models": models if not has_all else []
            }
    
    async def check_model_access(self, username: str, model_name: str) -> bool:
        """Check if user has access to specific model"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return False
            
            user_model_repo = UserModelRepository(session)
            return await user_model_repo.has_model_access(user.id, model_name)
    
    async def revoke_model(self, username: str, model_name: str) -> bool:
        """Revoke access to specific model"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")
            
            user_model_repo = UserModelRepository(session)
            result = await user_model_repo.revoke_model(user.id, model_name)
            
            if not result:
                raise ValueError(f"Model bulunamadı veya zaten yetkilendirilmemiş: {model_name}")
            
            return result
    
    async def revoke_all_models(self, username: str) -> bool:
        """Revoke all model access (including has_all_models)"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")
            
            user_model_repo = UserModelRepository(session)
            return await user_model_repo.revoke_all_models(user.id)


# Global user manager instance
user_manager = UserManager()

