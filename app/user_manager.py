"""User management and JWT token operations"""

import json
import os
import secrets
from datetime import datetime
from typing import Optional, List, Dict
import jwt
from pathlib import Path

from app.models import User, UserInDB
from app.config import get_settings


class UserManager:
    """Manage users and JWT tokens"""
    
    def __init__(self, users_file: str = "/app/data/users.json"):
        self.users_file = users_file
        self.settings = get_settings()
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        """Ensure users file exists"""
        if not os.path.exists(self.users_file):
            os.makedirs(os.path.dirname(self.users_file), exist_ok=True)
            self._save_db(UserInDB(users=[]))
    
    def _load_db(self) -> UserInDB:
        """Load users database from JSON file"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return UserInDB(**data)
        except Exception as e:
            print(f"Error loading users database: {e}")
            return UserInDB(users=[])
    
    def _save_db(self, db: UserInDB):
        """Save users database to JSON file"""
        try:
            with open(self.users_file, 'w', encoding='utf-8') as f:
                json.dump(db.model_dump(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving users database: {e}")
            raise
    
    def _generate_token(self, username: str) -> str:
        """
        Generate JWT token for user (no expiration)
        
        Args:
            username: Username
        
        Returns:
            JWT token string
        """
        payload = {
            "username": username,
            "iat": int(datetime.utcnow().timestamp())
        }
        token = jwt.encode(payload, self.settings.jwt_secret_key, algorithm="HS256")
        return token
    
    def create_user(self, username: str) -> Optional[User]:
        """
        Create a new user with JWT token
        
        Args:
            username: Username
        
        Returns:
            User object or None if user already exists
        """
        db = self._load_db()
        
        # Check if user already exists
        if any(u.username == username for u in db.users):
            return None
        
        # Generate token
        token = self._generate_token(username)
        
        # Create user
        user = User(
            username=username,
            token=token,
            created_at=datetime.utcnow().isoformat()
        )
        
        db.users.append(user)
        self._save_db(db)
        
        return user
    
    def delete_user(self, username: str) -> bool:
        """
        Delete a user
        
        Args:
            username: Username
        
        Returns:
            True if user was deleted, False if user not found
        """
        db = self._load_db()
        original_count = len(db.users)
        
        db.users = [u for u in db.users if u.username != username]
        
        if len(db.users) < original_count:
            self._save_db(db)
            return True
        
        return False
    
    def refresh_token(self, username: str) -> Optional[User]:
        """
        Refresh user's JWT token
        
        Args:
            username: Username
        
        Returns:
            Updated User object or None if user not found
        """
        db = self._load_db()
        
        user_index = None
        for i, u in enumerate(db.users):
            if u.username == username:
                user_index = i
                break
        
        if user_index is None:
            return None
        
        # Generate new token
        new_token = self._generate_token(username)
        
        # Update user
        db.users[user_index].token = new_token
        db.users[user_index].updated_at = datetime.utcnow().isoformat()
        
        self._save_db(db)
        
        return db.users[user_index]
    
    def get_user(self, username: str) -> Optional[User]:
        """
        Get user by username
        
        Args:
            username: Username
        
        Returns:
            User object or None if not found
        """
        db = self._load_db()
        
        for user in db.users:
            if user.username == username:
                return user
        
        return None
    
    def list_users(self) -> List[User]:
        """
        List all users
        
        Returns:
            List of User objects
        """
        db = self._load_db()
        return db.users
    
    def verify_token(self, token: str) -> Optional[str]:
        """
        Verify JWT token and return username
        
        Args:
            token: JWT token string
        
        Returns:
            Username if token is valid, None otherwise
        """
        try:
            payload = jwt.decode(token, self.settings.jwt_secret_key, algorithms=["HS256"])
            username = payload.get("username")
            
            if not username:
                return None
            
            # Check if user exists and token matches
            db = self._load_db()
            for user in db.users:
                if user.username == username and user.token == token:
                    return username
            
            return None
        except jwt.InvalidTokenError:
            return None
        except Exception as e:
            print(f"Error verifying token: {e}")
            return None


# Global user manager instance
user_manager = UserManager()

