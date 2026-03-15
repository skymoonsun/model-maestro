"""Shared fixtures for tests"""

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Optional, Any

# Set required environment variables BEFORE importing any app modules
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-testing")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")


class MockModelGroup:
    """Mock ModelGroup for testing"""
    def __init__(self, id=1, name="test-group", description=None, strategy="round_robin", is_active=True):
        self.id = id
        self.name = name
        self.description = description
        self.strategy = strategy
        self.is_active = is_active
        self.created_at = None
        self.updated_at = None


class MockModelGroupMember:
    """Mock ModelGroupMember for testing"""
    def __init__(
        self,
        id=1,
        group_id=1,
        model_display_name="test-model",
        capability_tags=None,
        weight=1,
        priority=0,
        is_fallback=False,
        is_active=True
    ):
        self.id = id
        self.group_id = group_id
        self.model_display_name = model_display_name
        self.capability_tags = capability_tags or []
        self.weight = weight
        self.priority = priority
        self.is_fallback = is_fallback
        self.is_active = is_active


@pytest.fixture
def mock_db_session():
    """Mock async database session"""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_model_group_repo():
    """Mock ModelGroupRepository"""
    repo = MagicMock()
    repo.create_group = AsyncMock()
    repo.get_group_by_name = AsyncMock()
    repo.get_all_groups = AsyncMock(return_value=[])
    repo.update_group = AsyncMock()
    repo.delete_group = AsyncMock()
    repo.get_group_with_members = AsyncMock()
    repo.add_member = AsyncMock()
    repo.remove_member = AsyncMock()
    repo.get_members_by_group_name = AsyncMock(return_value=[])
    repo.group_exists = AsyncMock(return_value=False)
    repo.get_vision_capable_member = AsyncMock()
    repo.get_fallback_member = AsyncMock()
    return repo


@pytest.fixture
def sample_model_group():
    """Sample model group for testing"""
    return MockModelGroup(
        id=1,
        name="smart-models",
        description="Smart models group",
        strategy="round_robin",
        is_active=True
    )


@pytest.fixture
def sample_group_members():
    """Sample group members for testing"""
    return [
        MockModelGroupMember(
            id=1,
            group_id=1,
            model_display_name="glm-5:cloud",
            capability_tags=["vision", "code"],
            weight=2,
            priority=0,
            is_fallback=False,
            is_active=True
        ),
        MockModelGroupMember(
            id=2,
            group_id=1,
            model_display_name="kimi-k2.5:cloud",
            capability_tags=["vision"],
            weight=1,
            priority=1,
            is_fallback=False,
            is_active=True
        ),
        MockModelGroupMember(
            id=3,
            group_id=1,
            model_display_name="qwen3.5:cloud",
            capability_tags=["code"],
            weight=1,
            priority=2,
            is_fallback=True,
            is_active=True
        ),
    ]


@pytest.fixture
def mock_async_session_maker(mock_db_session):
    """Mock async_session_maker that returns the mock session"""
    class AsyncContextManager:
        async def __aenter__(self):
            return mock_db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    return MagicMock(return_value=AsyncContextManager())