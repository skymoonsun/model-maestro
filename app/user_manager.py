"""User management and JWT token operations (PostgreSQL-based)"""

from datetime import datetime
import secrets
import uuid
import logging
from typing import Optional, List
import jwt

from app.config import get_settings
from app.database import async_session_maker
from app.repositories import UserRepository, UserModelRepository, UserActivityRepository, UserLimitRepository, UserNodeRepository, UserNodeModelRepository

logger = logging.getLogger(__name__)


class UserManager:
    """Manage users and JWT tokens with PostgreSQL"""
    
    def __init__(self):
        self.settings = get_settings()
    
    def _generate_token(self, username: str) -> str:
        """Generate JWT token for user (no expiration)"""
        payload = {
            "username": username,
            "iat": int(datetime.utcnow().timestamp()),
            "jti": str(uuid.uuid4()),  # Benzersiz JWT ID
            "sub": username,  # Subject (kullanıcı adı)
            "iss": "model-maestro",  # Issuer
            "aud": "llm-api"  # Audience
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
                
                # Cache the token -> username mapping
                from app.redis import redis_manager, CACHE_TTL
                import logging
                logger = logging.getLogger(__name__)
                
                if redis_manager:
                    cache_result = await redis_manager.set(f"token:{new_token}", username, expire=CACHE_TTL["TOKEN_USERNAME"])
                    logger.info(f"[REACTIVATE_USER] Redis cache write for user '{username}': {'SUCCESS' if cache_result else 'FAILED'}")
                
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
            
            # Cache the token -> username mapping
            from app.redis import redis_manager, CACHE_TTL
            import logging
            logger = logging.getLogger(__name__)
            
            if redis_manager:
                cache_result = await redis_manager.set(f"token:{token}", username, expire=CACHE_TTL["TOKEN_USERNAME"])
                logger.info(f"[CREATE_USER] Redis cache write for user '{username}': {'SUCCESS' if cache_result else 'FAILED'}")
            
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
            await redis_manager.delete(f"user_node_access:{username}")
            await redis_manager.delete(f"user_node_model_access:{username}")
            
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
            # Decode token with signature verification only
            # Don't verify iss, aud, exp (we handle these ourselves)
            payload = jwt.decode(
                token,
                self.settings.jwt_secret_key,
                algorithms=["HS256"],
                options={
                    "verify_signature": True,
                    "verify_exp": False,  # No expiration
                    "verify_iss": False,  # Don't verify issuer
                    "verify_aud": False,  # Don't verify audience
                }
            )
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
        except jwt.InvalidTokenError as e:
            logger.error(f"JWT decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error verifying token: {e}")
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
        total_tokens: int = 0,
        source: str = None,
        url_path: str = None
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
                total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                source=source,
                url_path=url_path
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
                    "id": activity.id,
                    "model_name": activity.model_name,
                    "request_type": activity.request_type,
                    "prompt_tokens": activity.prompt_tokens,
                    "completion_tokens": activity.completion_tokens,
                    "total_tokens": activity.total_tokens,
                    "status_code": activity.status_code,
                    "duration_ms": activity.duration_ms,
                    "error_message": activity.error_message,
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

    # ========================================================================
    # Node Access Control
    # ========================================================================

    async def check_node_access(self, username: str, node_id: int) -> bool:
        """Check if user has access to specific node"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                return False

            user_node_repo = UserNodeRepository(session)
            return await user_node_repo.has_node_access(user.id, node_id)

    async def check_node_model_access(self, username: str, node_id: int, model_name: str) -> bool:
        """Check if user has access to specific node+model combination"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                return False

            user_node_model_repo = UserNodeModelRepository(session)
            return await user_node_model_repo.has_node_model_access(user.id, node_id, model_name)

    async def get_user_node_access(self, username: str) -> dict:
        """
        Get user's node access information (for caching)

        Returns:
            Dict with "has_all" boolean and "nodes" list
        """
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user_node_repo = UserNodeRepository(session)

            user = await user_repo.get_by_username(username)
            if not user:
                return {"has_all": False, "nodes": []}

            has_restriction = await user_node_repo.has_any_node_restriction(user.id)
            if not has_restriction:
                return {"has_all": True, "nodes": []}

            node_ids = await user_node_repo.get_user_nodes(user.id)
            return {"has_all": False, "nodes": node_ids}

    async def get_user_node_model_access(self, username: str) -> dict:
        """
        Get user's node-model access information (for caching)

        Returns:
            Dict with "has_all" boolean and "node_models" list of dicts
        """
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user_node_model_repo = UserNodeModelRepository(session)

            user = await user_repo.get_by_username(username)
            if not user:
                return {"has_all": False, "node_models": []}

            has_restriction = await user_node_model_repo.has_any_node_model_restriction(user.id)
            if not has_restriction:
                return {"has_all": True, "node_models": []}

            node_models = await user_node_model_repo.get_user_node_models(user.id)
            return {
                "has_all": False,
                "node_models": [{"node_id": nid, "model_name": mn} for nid, mn in node_models]
            }

    async def grant_node(self, username: str, node_id: int) -> bool:
        """Grant user access to specific node"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_repo = UserNodeRepository(session)
            await user_node_repo.assign_node(user.id, node_id)
            return True

    async def revoke_node(self, username: str, node_id: int) -> bool:
        """Revoke user access to specific node"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_repo = UserNodeRepository(session)
            result = await user_node_repo.revoke_node(user.id, node_id)
            if not result:
                raise ValueError(f"Node erişimi bulunamadı veya zaten yetkilendirilmemiş: {node_id}")
            return result

    async def grant_all_nodes(self, username: str) -> bool:
        """Grant user access to all nodes (clear restrictions)"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_repo = UserNodeRepository(session)
            return await user_node_repo.revoke_all_nodes(user.id)

    async def get_user_nodes(self, username: str) -> Optional[dict]:
        """Get user's allowed nodes with details"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                return None

            user_node_repo = UserNodeRepository(session)
            has_restriction = await user_node_repo.has_any_node_restriction(user.id)
            nodes = await user_node_repo.get_user_nodes_with_details(user.id)

            return {
                "username": username,
                "has_restriction": has_restriction,
                "nodes": nodes
            }

    async def grant_node_model(self, username: str, node_id: int, model_name: str) -> bool:
        """Grant user access to specific node+model combination"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_model_repo = UserNodeModelRepository(session)
            await user_node_model_repo.assign_node_model(user.id, node_id, model_name)
            return True

    async def revoke_node_model(self, username: str, node_id: int, model_name: str) -> bool:
        """Revoke user access to specific node+model combination"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_model_repo = UserNodeModelRepository(session)
            result = await user_node_model_repo.revoke_node_model(user.id, node_id, model_name)
            if not result:
                raise ValueError(f"Node-model erişimi bulunamadı veya zaten yetkilendirilmemiş: {node_id}/{model_name}")
            return result

    async def grant_all_node_models(self, username: str) -> bool:
        """Grant user access to all node-model combinations (clear restrictions)"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                raise ValueError(f"Kullanıcı bulunamadı: {username}")

            user_node_model_repo = UserNodeModelRepository(session)
            return await user_node_model_repo.revoke_all_node_models(user.id)

    async def get_user_node_models(self, username: str) -> Optional[dict]:
        """Get user's allowed node-model combinations with details"""
        async with async_session_maker() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_username(username)

            if not user:
                return None

            user_node_model_repo = UserNodeModelRepository(session)
            has_restriction = await user_node_model_repo.has_any_node_model_restriction(user.id)
            node_models = await user_node_model_repo.get_user_node_models_with_details(user.id)

            return {
                "username": username,
                "has_restriction": has_restriction,
                "node_models": node_models
            }


# Global user manager instance
user_manager = UserManager()

