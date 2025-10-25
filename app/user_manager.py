"""User management and JWT token operations (PostgreSQL-based)"""

from datetime import datetime
from typing import Optional, List
import jwt

from app.config import get_settings
from app.database import async_session_maker
from app.repositories import UserRepository, UserModelRepository, UserActivityRepository, UserLimitRepository


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
            
            # Invalidate Redis caches
            from app.redis import redis_manager, CACHE_KEYS
            if user.token:
                await redis_manager.delete(f"token:{user.token}")
            await redis_manager.delete(CACHE_KEYS["USER_ACCESS"].format(username=username))
            await redis_manager.delete(CACHE_KEYS["USER_LIMIT"].format(username=username))
            
            # Hard delete all user_models entries
            await user_model_repo.delete_all_for_user(user.id)
            
            # Soft delete user
            result = await user_repo.soft_delete(username)
            
            return result
    
    async def refresh_token(self, username: str) -> Optional[dict]:
        """Refresh user's JWT token"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            
            # Get old token
            old_user = await user_repo.get_by_username(username)
            old_token = old_user.token if old_user else None
            
            # Generate new token
            new_token = self._generate_token(username)
            
            # Update user
            user = await user_repo.update_token(username, new_token)
            
            if not user:
                return None
            
            # Invalidate old token cache and set new one
            from app.redis import redis_manager, CACHE_TTL
            if old_token:
                await redis_manager.delete(f"token:{old_token}")
            # Cache new token
            await redis_manager.set(f"token:{new_token}", username, expire=CACHE_TTL["TOKEN_USERNAME"])
            
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
    
    async def log_user_activity(
        self,
        username: str,
        model_name: str,
        request_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0
    ) -> bool:
        """Log user activity for token usage and model access"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return False
            
            activity_repo = UserActivityRepository(session)
            await activity_repo.log_activity(
                user_id=user.id,
                model_name=model_name,
                request_type=request_type,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens or (prompt_tokens + completion_tokens)
            )
            
            await session.commit()
            return True
    
    async def get_user_activity(
        self,
        username: str,
        limit: int = 100,
        offset: int = 0
    ) -> Optional[List[dict]]:
        """Get user activity logs"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            activity_repo = UserActivityRepository(session)
            activities = await activity_repo.get_user_activity(user.id, limit, offset)
            
            return [
                {
                    "model_name": activity.model_name,
                    "request_type": activity.request_type,
                    "prompt_tokens": activity.prompt_tokens,
                    "completion_tokens": activity.completion_tokens,
                    "total_tokens": activity.total_tokens,
                    "created_at": activity.created_at.isoformat() if activity.created_at else None
                }
                for activity in activities
            ]
    
    async def get_user_token_usage(
        self,
        username: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Optional[dict]:
        """Get user token usage statistics"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            activity_repo = UserActivityRepository(session)
            return await activity_repo.get_user_token_usage(user.id, start_date, end_date)
    
    async def get_user_model_usage(
        self,
        username: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Optional[List[dict]]:
        """Get user model usage statistics"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            activity_repo = UserActivityRepository(session)
            return await activity_repo.get_user_model_usage(user.id, start_date, end_date)
    
    async def set_user_limit(
        self,
        username: str,
        request_limit: Optional[int] = None,
        token_limit: Optional[int] = None
    ) -> Optional[dict]:
        """Set user request and token limits"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            limit_repo = UserLimitRepository(session)
            user_limit = await limit_repo.set_user_limit(user.id, request_limit, token_limit)
            
            await session.commit()
            
            return {
                "username": username,
                "request_limit": user_limit.request_limit,
                "token_limit": user_limit.token_limit,
                "created_at": user_limit.created_at.isoformat() if user_limit.created_at else None,
                "updated_at": user_limit.updated_at.isoformat() if user_limit.updated_at else None
            }
    
    async def get_user_limit(self, username: str) -> Optional[dict]:
        """Get user request and token limits"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return None
            
            limit_repo = UserLimitRepository(session)
            user_limit = await limit_repo.get_user_limit(user.id)
            
            if not user_limit:
                return {
                    "username": username,
                    "request_limit": None,
                    "token_limit": None,
                    "created_at": None,
                    "updated_at": None
                }
            
            return {
                "username": username,
                "request_limit": user_limit.request_limit,
                "token_limit": user_limit.token_limit,
                "created_at": user_limit.created_at.isoformat() if user_limit.created_at else None,
                "updated_at": user_limit.updated_at.isoformat() if user_limit.updated_at else None
            }
    
    async def remove_user_limit(self, username: str) -> bool:
        """Remove user limits"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)
            
            if not user:
                return False
            
            limit_repo = UserLimitRepository(session)
            result = await limit_repo.remove_user_limit(user.id)
            
            if result:
                await session.commit()
            
            return result
    
    async def get_user_model_access(self, username: str) -> dict:
        """
        Get user's model access information (for caching)
        
        Returns:
            Dict with "has_all" boolean and "models" list
        """
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user_model_repo = UserModelRepository(session)
            
            user = await user_repo.get_by_username(username)
            if not user:
                return {"has_all": False, "models": []}
            
            has_all = await user_model_repo.has_all_models(user.id)
            if has_all:
                return {"has_all": True, "models": []}
            
            user_models = await user_model_repo.get_user_models(user.id)
            return {
                "has_all": False,
                "models": [um.model_name for um in user_models]
            }


# Global user manager instance
user_manager = UserManager()

