"""SQLAlchemy database models"""

from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    token = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, default=True, nullable=False)
    
    # Relationships
    user_models = relationship("UserModel", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(username='{self.username}')>"


class ModelMapping(Base):
    """Model name mapping"""
    __tablename__ = "model_mappings"
    
    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String(255), unique=True, nullable=False, index=True)
    real_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<ModelMapping(display_name='{self.display_name}', real_name='{self.real_name}')>"


class UserModel(Base):
    """User-Model relationship"""
    __tablename__ = "user_models"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model_display_name = Column(String(255), nullable=True)
    has_all_models = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="user_models")
    
    # Unique constraint
    __table_args__ = (
        UniqueConstraint('user_id', 'model_display_name', name='uq_user_model'),
    )
    
    def __repr__(self):
        return f"<UserModel(user_id={self.user_id}, model='{self.model_display_name}', all={self.has_all_models})>"

